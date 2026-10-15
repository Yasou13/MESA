"""Resumable sequential verification; never count interrupted/resource-limited work as PASS."""
from pathlib import Path
import json, os, signal, subprocess, time, xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / 'bounded-tests'
OUT.mkdir(exist_ok=True)
files = sorted(str(p.relative_to(ROOT)) for p in (ROOT/'tests').rglob('test_*.py') if 'go_live_proofs' not in p.parts and 'bench' not in p.parts)
manifest = [files[i:i+6] for i in range(0,len(files),6)]
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
env=os.environ.copy()
env.update(MESA_RUNTIME_PROFILE='test-isolated', MESA_RUNTIME_LAB_ROOT='/tmp/mesa-v080-bounded', MESA_STORAGE_ROOT='/tmp/mesa-v080-bounded/storage', MESA_LOAD_DOTENV='false', MESA_MODEL_ENABLED='false', MESA_EXTERNAL_PROVIDER_ENABLED='false', HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1', MPLCONFIGDIR='/tmp/mesa-v080-mpl', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', PYTHONUNBUFFERED='1')
env.pop('MESA_PYTEST_FORCE_EXIT_COMPAT',None)
Path(env['MESA_STORAGE_ROOT']).mkdir(parents=True,exist_ok=True)

def usage(pid):
    pending=[pid];rss=0
    while pending:
        current=pending.pop();base=Path('/proc')/str(current)
        try:
            for line in (base/'status').read_text().splitlines():
                if line.startswith('VmRSS:'):rss+=int(line.split()[1])
            for task in (base/'task').iterdir():
                pending.extend(int(x) for x in (task/'children').read_text().split())
        except (OSError,PermissionError):pass
    return rss

def available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):return int(line.split()[1])
    return 0

results=[]
for i,group in enumerate(manifest,1):
    stem=OUT/f'shard-{i:03}'
    result_path=stem.with_suffix('.json')
    if result_path.exists():
        prior=json.loads(result_path.read_text())
        if prior['exit_code']==0 and prior['files']==group:results.append(prior);print('RESUME',i,'PASS',flush=True);continue
    junit=stem.with_suffix('.xml')
    cmd=[str(ROOT/'.venv/bin/python'),'-m','pytest','-q','-m','not live_external','-o','faulthandler_timeout=90','--junitxml='+str(junit),*group]
    started=time.monotonic();peak=0;reason=None
    with stem.with_suffix('.log').open('w') as log:
        proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        while proc.poll() is None:
            peak=max(peak,usage(proc.pid))
            if peak>1800*1024:reason='RSS_LIMIT_1800_MiB'
            elif available()<3*1024*1024:reason='SYSTEM_AVAILABLE_BELOW_3_GiB'
            elif time.monotonic()-started>600:reason='SHARD_TIMEOUT_600s'
            if reason:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                break
            time.sleep(0.5)
    counts={}
    if junit.exists():
        suites=ET.parse(junit).getroot()
        counts={k:sum(int(s.attrib.get(k,0)) for s in suites.iter('testsuite')) for k in ['tests','failures','errors','skipped']}
    result={'shard':i,'files':group,'command':cmd,'exit_code':proc.returncode,'interruption':reason,'peak_rss_MiB':round(peak/1024,1),'seconds':round(time.monotonic()-started,2),'counts':counts}
    result_path.write_text(json.dumps(result,indent=2));results.append(result)
    (OUT/'progress.json').write_text(json.dumps({'completed':len(results),'total':len(manifest),'results':results},indent=2))
    print(f'SHARD {i}/{len(manifest)} exit={proc.returncode} peak={result["peak_rss_MiB"]}MiB counts={counts} reason={reason}',flush=True)
    if reason:break
summary={'completed_shards':len(results),'total_shards':len(manifest),'complete':len(results)==len(manifest),'pass':len(results)==len(manifest) and all(r['exit_code']==0 for r in results),'peak_rss_MiB':max((r['peak_rss_MiB'] for r in results),default=0),'totals':{k:sum(r['counts'].get(k,0) for r in results) for k in ['tests','failures','errors','skipped']}}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
raise SystemExit(0 if summary['pass'] else 1)
