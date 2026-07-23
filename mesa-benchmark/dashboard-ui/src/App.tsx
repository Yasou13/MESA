import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type {
  Catalog,
  ClientInfo,
  DatasetInfo,
  DatasetScenario,
  Job,
  JobDiagnostics,
  OllamaSettings,
  PlanPreview,
  QuestionRow,
  ResultSummary,
  SystemSnapshot,
} from "./types";

type View =
  | "overview"
  | "new"
  | "runs"
  | "results"
  | "datasets"
  | "clients"
  | "guide"
  | "system";

const navigation: Array<{ id: View; label: string; icon: string }> = [
  { id: "overview", label: "Genel Bakış", icon: "⌂" },
  { id: "new", label: "Yeni Benchmark", icon: "+" },
  { id: "runs", label: "Çalışmalar", icon: "◫" },
  { id: "results", label: "Sonuçlar", icon: "⌁" },
  { id: "datasets", label: "Datasetler", icon: "◇" },
  { id: "clients", label: "Client’lar", icon: "∷" },
  { id: "guide", label: "Rehber", icon: "?" },
  { id: "system", label: "Sistem", icon: "◉" },
];

function formatDuration(value: number | null): string {
  if (value === null) return "Hesaplanıyor";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  return [hours, minutes, seconds]
    .map((item) => item.toString().padStart(2, "0"))
    .join(":");
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function StatusBadge({ status }: { status: Job["status"] }) {
  const labels: Record<Job["status"], string> = {
    queued: "Kuyrukta",
    running: "Çalışıyor",
    paused: "Duraklatıldı",
    cancelled: "Durduruldu",
    failed: "Başarısız",
    completed: "Doğrulandı",
  };
  return <span className={`badge badge--${status}`}>{labels[status]}</span>;
}

function Meter({
  label,
  value,
  detail,
}: {
  label: string;
  value: number;
  detail?: string;
}) {
  return (
    <div className="meter">
      <div className="meter__head">
        <span>{label}</span>
        <strong>{detail ?? `${Math.round(value)}%`}</strong>
      </div>
      <div className="meter__track">
        <div className="meter__fill" style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
    </div>
  );
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: () => void;
}) {
  return (
    <div className="empty">
      <span className="empty__mark">M</span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action && (
        <button className="button button--primary" onClick={action}>
          Benchmark oluştur
        </button>
      )}
    </div>
  );
}

function Overview({
  jobs,
  system,
  onNew,
  onOpen,
}: {
  jobs: Job[];
  system: SystemSnapshot | null;
  onNew: () => void;
  onOpen: (job: Job) => void;
}) {
  const active = jobs.find((job) =>
    ["running", "paused", "queued"].includes(job.status),
  );
  const latest = jobs.slice(0, 5);
  if (!active && !jobs.length) {
    return (
      <EmptyState
        title="Kontrol sende, gürültü değil"
        body="İlk benchmark planını oluştur; preflight, sharding ve doğrulama tek akışta yönetilsin."
        action={onNew}
      />
    );
  }
  return (
    <div className="stack stack--large">
      <section className="hero panel">
        <div className="hero__copy">
          <div className="eyebrow">AKTİF ÇALIŞMA</div>
          <div className="hero__title-row">
            <h2>{active?.name ?? "Aktif benchmark yok"}</h2>
            {active && <StatusBadge status={active.status} />}
          </div>
          {active ? (
            <>
              <p>
                {active.current_task ?? "Shard hazırlanıyor"} · ETA güveni{" "}
                <strong>{active.eta_confidence}</strong>
              </p>
              <Meter
                label="Genel ilerleme"
                value={active.progress}
                detail={`%${active.progress.toFixed(1)}`}
              />
              <div className="hero__meta">
                <span>
                  <small>Kalan süre</small>
                  {formatDuration(active.eta_seconds)}
                </span>
                <span>
                  <small>Profil</small>
                  {active.profile}
                </span>
                <button className="button button--quiet" onClick={() => onOpen(active)}>
                  Çalışmayı aç →
                </button>
              </div>
            </>
          ) : (
            <button className="button button--primary" onClick={onNew}>
              Yeni benchmark
            </button>
          )}
        </div>
        <div className="hero__signal" aria-hidden="true">
          <div className="signal-ring">
            <span>{Math.round(active?.progress ?? 0)}</span>
            <small>%</small>
          </div>
        </div>
      </section>

      <div className="grid grid--3">
        <section className="panel stat-panel">
          <div className="panel__label">OLLAMA</div>
          <strong className={system?.ollama.online ? "text-good" : "text-bad"}>
            {system?.ollama.online ? "Bağlı" : "Çevrimdışı"}
          </strong>
          <p>{system?.ollama.model ?? "Model bekleniyor"}</p>
        </section>
        <section className="panel stat-panel">
          <div className="panel__label">SİSTEM YÜKÜ</div>
          <strong>%{Math.round(system?.cpu_percent ?? 0)}</strong>
          <p>CPU · RAM %{Math.round(system?.memory_percent ?? 0)}</p>
        </section>
        <section className="panel stat-panel">
          <div className="panel__label">DOĞRULANAN</div>
          <strong>{jobs.filter((job) => job.status === "completed").length}</strong>
          <p>{jobs.length} toplam çalışma</p>
        </section>
      </div>

      <section className="panel">
        <div className="section-head">
          <div>
            <div className="eyebrow">GEÇMİŞ</div>
            <h3>Son çalışmalar</h3>
          </div>
          <button className="button button--quiet" onClick={onNew}>
            + Yeni benchmark
          </button>
        </div>
        <JobTable jobs={latest} onOpen={onOpen} />
      </section>
    </div>
  );
}

function JobTable({
  jobs,
  onOpen,
}: {
  jobs: Job[];
  onOpen: (job: Job) => void;
}) {
  if (!jobs.length)
    return <p className="muted">Henüz kayıtlı çalışma bulunmuyor.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Çalışma</th>
            <th>Profil</th>
            <th>Durum</th>
            <th>İlerleme</th>
            <th>Başlangıç</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <strong>{job.name}</strong>
                <small>{job.current_task ?? job.id.slice(0, 8)}</small>
              </td>
              <td className="capitalize">{job.profile}</td>
              <td>
                <StatusBadge status={job.status} />
              </td>
              <td className="progress-cell">
                <div className="micro-progress">
                  <i style={{ width: `${job.progress}%` }} />
                </div>
                %{job.progress.toFixed(1)}
              </td>
              <td>{formatDate(job.created_at)}</td>
              <td>
                <button className="icon-button" onClick={() => onOpen(job)}>
                  →
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewBenchmark({
  catalog,
  ollama,
  onCreated,
}: {
  catalog: Catalog | null;
  ollama: OllamaSettings | null;
  onCreated: (job: Job) => void;
}) {
  const [step, setStep] = useState(1);
  const [name, setName] = useState("Quality benchmark");
  const [profile, setProfile] = useState("quality");
  const [config, setConfig] = useState(
    "resource://configs/internal/smoke_dense.yaml",
  );
  const [clients, setClients] = useState<string[]>([
    "mesa",
    "dense-rag",
    "mem0",
  ]);
  const [shardMode, setShardMode] = useState("auto_duration");
  const [targetMinutes, setTargetMinutes] = useState(20);
  const [shardCount, setShardCount] = useState(4);
  const [questionLimit, setQuestionLimit] = useState(100);
  const [contextLimit, setContextLimit] = useState(1000);
  const [timeLimit, setTimeLimit] = useState<number | null>(null);
  const [iterations, setIterations] = useState(1);
  const [seed, setSeed] = useState(42);
  const [topK, setTopK] = useState(5);
  const [tokenBudget, setTokenBudget] = useState(4096);
  const [generationEnabled, setGenerationEnabled] = useState(false);
  const [generatorModel, setGeneratorModel] = useState("");
  const [temperature, setTemperature] = useState(0);
  const [judgeEnabled, setJudgeEnabled] = useState(false);
  const [judgeModel, setJudgeModel] = useState("");
  const [warmupEnabled, setWarmupEnabled] = useState(true);
  const [draggedClient, setDraggedClient] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!generatorModel && ollama?.model) setGeneratorModel(ollama.model);
  }, [generatorModel, ollama?.model]);

  const body = useMemo(
    () => ({
      name,
      profile,
      config,
      clients,
      seed,
      iterations,
      top_k: topK,
      context_token_budget: tokenBudget,
      generation_enabled: profile === "capacity" ? false : generationEnabled,
      generator_model: generatorModel || null,
      generation_temperature: temperature,
      judge_enabled: profile === "capacity" ? false : judgeEnabled,
      judge_model: judgeModel || null,
      shard_mode: shardMode,
      target_shard_minutes: targetMinutes,
      shard_count: shardMode === "fixed_count" ? shardCount : null,
      shard_question_limit: questionLimit,
      shard_context_limit: contextLimit,
      time_limit_minutes: timeLimit,
      warmup_enabled: profile === "capacity" ? false : warmupEnabled,
    }),
    [
      name,
      profile,
      config,
      clients,
      seed,
      iterations,
      topK,
      tokenBudget,
      generationEnabled,
      generatorModel,
      temperature,
      judgeEnabled,
      judgeModel,
      shardMode,
      targetMinutes,
      shardCount,
      questionLimit,
      contextLimit,
      timeLimit,
      warmupEnabled,
    ],
  );

  const runPreview = async () => {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.preview(body));
      setStep(6);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.create(body));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleClient = (client: ClientInfo) => {
    if (!client.available) return;
    setClients((current) =>
      current.includes(client.id)
        ? current.filter((id) => id !== client.id)
        : [...current, client.id],
    );
  };

  const moveClient = (clientId: string, direction: -1 | 1) => {
    setClients((current) => {
      const index = current.indexOf(clientId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const dropClient = (targetId: string) => {
    if (!draggedClient || draggedClient === targetId) return;
    setClients((current) => {
      const next = current.filter((id) => id !== draggedClient);
      next.splice(next.indexOf(targetId), 0, draggedClient);
      return next;
    });
    setDraggedClient(null);
  };

  const selectedDataset = catalog?.datasets.find((item) => item.config === config);
  const steps = ["Profil", "Dataset", "Parçalama", "Client sırası", "Ayarlar", "Preflight"];

  return (
    <div className="wizard">
      <div className="wizard__steps">
        {steps.map((label, index) => (
          <button
            key={label}
            className={step === index + 1 ? "active" : step > index + 1 ? "done" : ""}
            onClick={() => index + 1 < step && setStep(index + 1)}
          >
            <span>{step > index + 1 ? "✓" : index + 1}</span>
            {label}
          </button>
        ))}
      </div>

      <section className="panel wizard__panel">
        {step === 1 && (
          <>
            <div className="eyebrow">ADIM 1 / 6</div>
            <h2>Ne ölçmek istiyorsun?</h2>
            <p className="lead">
              Profil seçimi ingest semantiğini ve hangi metriklerin karşılaştırılabilir
              olduğunu kilitler.
            </p>
            <label className="field">
              <span>Çalışma adı</span>
              <input value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <div className="choice-grid">
              {catalog?.profiles.map((item) => (
                <button
                  key={item.id}
                  className={`choice ${profile === item.id ? "selected" : ""}`}
                  onClick={() => {
                    setProfile(item.id);
                    if (item.id === "native") {
                      setClients(["mesa", "mem0"]);
                      setGenerationEnabled(true);
                    }
                    if (item.id === "quality") {
                      setClients(["mesa", "dense-rag", "mem0"]);
                      setGenerationEnabled(false);
                    }
                    if (item.id === "capacity") {
                      setGenerationEnabled(false);
                      setJudgeEnabled(false);
                      setWarmupEnabled(false);
                    }
                  }}
                >
                  <span className="choice__radio" />
                  <strong>{item.name}</strong>
                  <p>{item.description}</p>
                </button>
              ))}
            </div>
            <div className="wizard__actions">
              <span />
              <button className="button button--primary" onClick={() => setStep(2)}>
                Dataset seç →
              </button>
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <div className="eyebrow">ADIM 2 / 6</div>
            <h2>Hangi dataset kullanılacak?</h2>
            <p className="lead">
              Dataset amacı, büyüklüğü ve hazır olup olmadığı seçimden önce görünür.
            </p>
            <div className="dataset-list">
              {catalog?.datasets.map((dataset) => (
                <button
                  key={dataset.id}
                  disabled={!dataset.ready}
                  className={config === dataset.config ? "selected" : ""}
                  onClick={() => setConfig(dataset.config)}
                >
                  <span className="dataset-icon">◇</span>
                  <span>
                    <strong>{dataset.name}</strong>
                    <small>
                      {dataset.group} · {dataset.counts.questions} soru ·{" "}
                      {dataset.ready ? "hazır" : "sync gerekli"}
                    </small>
                  </span>
                  <i>{config === dataset.config ? "✓" : ""}</i>
                </button>
              ))}
            </div>
            {selectedDataset && (
              <>
                <div className="protocol-note">
                  <span>i</span>
                  <p>
                    <strong>{selectedDataset.name}:</strong> {selectedDataset.purpose}
                  </p>
                </div>
                {selectedDataset.metrics.supported.some((metric) =>
                  ["full_qa", "rubric_score"].includes(metric),
                ) && (
                  <div className="alert alert--warning">
                    Bu dataset semantik/rubric değerlendirmesi ister. Ollama
                    bağlantısı ve bağımsız judge modeli olmadan preflight geçmez.
                  </div>
                )}
              </>
            )}
            <div className="wizard__actions">
              <button className="button button--quiet" onClick={() => setStep(1)}>
                ← Geri
              </button>
              <button className="button button--primary" onClick={() => setStep(3)}>
                Parçalamayı ayarla →
              </button>
            </div>
          </>
        )}
        {step === 3 && (
          <>
            <div className="eyebrow">ADIM 3 / 6</div>
            <h2>Dataset nasıl parçalansın?</h2>
            <p className="lead">
              Scenario’lar bölünmez; bütün client’lar aynı deterministic shard planını kullanır.
            </p>
            <div className="choice-grid compact-choices">
              {[
                ["auto_duration", "Otomatik süre", "Geçmiş hız varsa hedef dakika; yoksa 100 soru / 1.000 context."],
                ["fixed_count", "Sabit parça", "Scenario’ları belirlediğin sayıda dengeli shard’a dağıtır."],
                ["limits", "Soru / context limiti", "Her shard için iki üst sınırı doğrudan belirlersin."],
              ].map(([id, title, description]) => (
                <button
                  key={id}
                  className={`choice ${shardMode === id ? "selected" : ""}`}
                  onClick={() => setShardMode(id)}
                >
                  <span className="choice__radio" />
                  <strong>{title}</strong>
                  <p>{description}</p>
                </button>
              ))}
            </div>
            <div className="grid grid--2 settings-row">
              {shardMode === "auto_duration" && (
                <label className="field">
                  <span>Shard hedef süresi <b title="Tahmini aktif çalışma süresidir.">?</b></span>
                  <input
                    type="number"
                    value={targetMinutes}
                    onChange={(event) => setTargetMinutes(Number(event.target.value))}
                  />
                  <small className="field-help">Varsayılan 20 dakika; geçmiş yoksa güven düşük gösterilir.</small>
                </label>
              )}
              {shardMode === "fixed_count" && (
                <label className="field">
                  <span>Toplam shard sayısı</span>
                  <input
                    type="number"
                    value={shardCount}
                    onChange={(event) => setShardCount(Number(event.target.value))}
                  />
                </label>
              )}
              {shardMode === "limits" && (
                <>
                  <label className="field">
                    <span>Shard başına soru</span>
                    <input
                      type="number"
                      value={questionLimit}
                      onChange={(event) => setQuestionLimit(Number(event.target.value))}
                    />
                  </label>
                  <label className="field">
                    <span>Shard başına context</span>
                    <input
                      type="number"
                      value={contextLimit}
                      onChange={(event) => setContextLimit(Number(event.target.value))}
                    />
                  </label>
                </>
              )}
            </div>
            <div className="wizard__actions">
              <button className="button button--quiet" onClick={() => setStep(2)}>← Geri</button>
              <button className="button button--primary" onClick={() => setStep(4)}>Client sırası →</button>
            </div>
          </>
        )}
        {step === 4 && (
          <>
            <div className="eyebrow">ADIM 4 / 6</div>
            <h2>Karşılaştırılacak sistemler ve sıra</h2>
            <p className="lead">
              Hazır client’ları seç; aşağıdaki sıra her shard’da aynen ve tek tek uygulanır.
            </p>
            <div className="client-grid">
              {catalog?.clients.map((client) => (
                <button
                  key={client.id}
                  disabled={!client.available}
                  className={`client-choice ${
                    clients.includes(client.id) ? "selected" : ""
                  }`}
                  onClick={() => toggleClient(client)}
                >
                  <span className={`health-dot ${client.available ? "online" : ""}`} />
                  <strong>{client.name}</strong>
                  <small>{client.available ? "Hazır" : client.reason}</small>
                  <i>{clients.includes(client.id) ? "✓" : ""}</i>
                </button>
              ))}
            </div>
            <div className="client-order">
              {clients.map((clientId, index) => {
                const client = catalog?.clients.find((item) => item.id === clientId);
                return (
                  <div
                    key={clientId}
                    draggable
                    onDragStart={() => setDraggedClient(clientId)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={() => dropClient(clientId)}
                  >
                    <span>{index + 1}</span>
                    <strong>{client?.name ?? clientId}</strong>
                    <small>Shard başına sıra {index + 1}</small>
                    <button
                      aria-label={`${clientId} yukarı`}
                      disabled={index === 0}
                      onClick={() => moveClient(clientId, -1)}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={`${clientId} aşağı`}
                      disabled={index === clients.length - 1}
                      onClick={() => moveClient(clientId, 1)}
                    >
                      ↓
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="protocol-note">
              <span>i</span>
              <p>
                Doğruluk öncelikli modda client’lar sıralı çalışır. Aynı Ollama
                kaynağında latency karşılaştırması kirlenmez.
              </p>
            </div>
            <div className="wizard__actions">
              <button className="button button--quiet" onClick={() => setStep(3)}>
                ← Geri
              </button>
              <button
                className="button button--primary"
                disabled={!clients.length}
                onClick={() => setStep(5)}
              >
                Ayarları yap →
              </button>
            </div>
          </>
        )}
        {step === 5 && (
          <>
            <div className="eyebrow">ADIM 5 / 6</div>
            <h2>Süre ve benchmark ayarları</h2>
            <p className="lead">
              Süre dolunca mevcut soru tamamlanır ve iş güvenli checkpoint’te duraklatılır.
            </p>
            <div className="time-presets">
              {[
                [null, "Limitsiz"],
                [15, "15 dk"],
                [30, "30 dk"],
                [60, "1 saat"],
              ].map(([value, label]) => (
                <button
                  key={String(label)}
                  className={timeLimit === value ? "selected" : ""}
                  onClick={() => setTimeLimit(value as number | null)}
                >
                  {label}
                </button>
              ))}
              <label>
                Özel
                <input
                  type="number"
                  placeholder="dakika"
                  value={
                    timeLimit && ![15, 30, 60].includes(timeLimit) ? timeLimit : ""
                  }
                  onChange={(event) =>
                    setTimeLimit(event.target.value ? Number(event.target.value) : null)
                  }
                />
              </label>
            </div>
            <details className="advanced-settings">
              <summary>Gelişmiş ayarlar</summary>
              <div className="grid grid--3">
                <label className="field">
                  <span>Iteration <b title="Aynı protokolün tekrar sayısıdır.">?</b></span>
                  <input type="number" value={iterations} onChange={(event) => setIterations(Number(event.target.value))} />
                </label>
                <label className="field">
                  <span>Seed <b title="Shard ve tekrar üretilebilirlik sabitidir.">?</b></span>
                  <input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
                </label>
                <label className="field">
                  <span>Top-K <b title="Retriever'ın döndüreceği context sayısıdır.">?</b></span>
                  <input type="number" value={topK} onChange={(event) => setTopK(Number(event.target.value))} />
                </label>
                <label className="field">
                  <span>Context token bütçesi</span>
                  <input type="number" value={tokenBudget} onChange={(event) => setTokenBudget(Number(event.target.value))} />
                </label>
                <label className="field toggle-field">
                  <span>Generation</span>
                  <input type="checkbox" checked={generationEnabled} disabled={profile === "capacity"} onChange={(event) => setGenerationEnabled(event.target.checked)} />
                  <small className="field-help">Retrieval contextinden ortak LLM cevabı üretir.</small>
                </label>
                <label className="field">
                  <span>Generator model</span>
                  <input list="ollama-models" value={generatorModel} disabled={!generationEnabled || profile === "capacity"} onChange={(event) => setGeneratorModel(event.target.value)} />
                </label>
                <label className="field">
                  <span>Temperature</span>
                  <input type="number" step="0.1" value={temperature} disabled={!generationEnabled || profile === "capacity"} onChange={(event) => setTemperature(Number(event.target.value))} />
                </label>
                <label className="field toggle-field">
                  <span>Bağımsız judge</span>
                  <input type="checkbox" checked={judgeEnabled} disabled={profile === "capacity"} onChange={(event) => setJudgeEnabled(event.target.checked)} />
                  <small className="field-help">Generator’dan farklı bir model seçilmelidir.</small>
                </label>
                <label className="field">
                  <span>Judge model</span>
                  <input list="ollama-models" value={judgeModel} disabled={!judgeEnabled || profile === "capacity"} onChange={(event) => setJudgeModel(event.target.value)} />
                </label>
                <label className="field toggle-field">
                  <span>Skorlanmayan warm-up</span>
                  <input type="checkbox" checked={warmupEnabled} disabled={profile === "capacity"} onChange={(event) => setWarmupEnabled(event.target.checked)} />
                </label>
              </div>
              <datalist id="ollama-models">
                {ollama?.models.map((model) => <option key={model} value={model} />)}
              </datalist>
            </details>
            <div className="wizard__actions">
              <button className="button button--quiet" onClick={() => setStep(4)}>← Geri</button>
              <button
                className="button button--primary"
                disabled={!clients.length || busy}
                onClick={runPreview}
              >
                {busy ? "Preflight çalışıyor…" : "Preflight →"}
              </button>
            </div>
          </>
        )}
        {step === 6 && preview && (
          <>
            <div className="eyebrow">ADIM 6 / 6</div>
            <h2>Çalıştırma planı hazır</h2>
            <p className="lead">
              Plan, dataset checksum ve seed ile sabitlendi. Başarısız shard’lar bağımsız
              tekrar çalıştırılabilir.
            </p>
            <div className="review-grid">
              <div>
                <small>Dataset</small>
                <strong>{preview.dataset.name}</strong>
              </div>
              <div>
                <small>İş sayısı</small>
                <strong>{preview.tasks}</strong>
              </div>
              <div>
                <small>Shard</small>
                <strong>{preview.shards.length}</strong>
              </div>
              <div>
                <small>Kapsam</small>
                <strong>{preview.dataset.questions} soru</strong>
              </div>
              <div>
                <small>Tahmini toplam</small>
                <strong>{formatDuration(preview.estimated_total_seconds)}</strong>
              </div>
              <div>
                <small>ETA güveni</small>
                <strong className="capitalize">{preview.eta_confidence}</strong>
              </div>
            </div>
            <div className="shard-strip">
              {preview.shards.map((shard) => (
                <span
                  key={shard.index}
                  title={`${shard.questions} soru · ${
                    shard.estimated_seconds
                      ? formatDuration(Math.round(shard.estimated_seconds))
                      : "süre geçmişi yok"
                  }`}
                >
                  {String(shard.index).padStart(2, "0")}
                </span>
              ))}
            </div>
            {preview.blockers.length > 0 && (
              <div className="alert alert--bad">{preview.blockers.join(" · ")}</div>
            )}
            {timeLimit &&
              preview.estimated_total_seconds &&
              timeLimit * 60 < preview.estimated_total_seconds && (
                <div className="alert alert--warning">
                  Süre limiti tahmini toplamdan kısa. İş yaklaşık %
                  {Math.max(
                    1,
                    Math.floor(
                      (timeLimit * 60 * 100) / preview.estimated_total_seconds,
                    ),
                  )}{" "}
                  kapsamda güvenli biçimde duraklayabilir.
                </div>
              )}
            <div className="wizard__actions">
              <button className="button button--quiet" onClick={() => setStep(5)}>
                ← Düzenle
              </button>
              <button
                className="button button--primary"
                disabled={!preview.ready || busy}
                onClick={create}
              >
                {busy ? "Başlatılıyor…" : "Benchmark’ı başlat"}
              </button>
            </div>
          </>
        )}
        {error && <div className="alert alert--bad">{error}</div>}
      </section>
    </div>
  );
}

function JobDetail({
  job,
  onAction,
  onNew,
}: {
  job: Job;
  onAction: () => void;
  onNew: () => void;
}) {
  const [detail, setDetail] = useState<Job>(job);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [streamConnected, setStreamConnected] = useState(false);
  const [diagnostics, setDiagnostics] = useState<JobDiagnostics | null>(null);

  useEffect(() => {
    let closed = false;
    const refresh = async () => {
      try {
        const value = await api.job(job.id);
        if (!closed) setDetail(value);
      } catch {
        // The global refresh surface will report connectivity.
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 1500);
    const source = new EventSource(`/api/jobs/${job.id}/events`);
    source.onopen = () => setStreamConnected(true);
    source.onerror = () => setStreamConnected(false);
    source.onmessage = (message) => {
      const value = JSON.parse(message.data) as Record<string, unknown>;
      setEvents((current) => [...current.slice(-39), value]);
    };
    return () => {
      closed = true;
      window.clearInterval(timer);
      source.close();
    };
  }, [job.id]);

  useEffect(() => {
    if (!["failed", "completed", "paused"].includes(detail.status)) return;
    api
      .diagnostics(job.id)
      .then(setDiagnostics)
      .catch(() => setDiagnostics(null));
  }, [detail.status, job.id]);

  const action = async (operation: "pause" | "cancel" | "resume" | "retry") => {
    setBusy(true);
    try {
      if (operation === "pause" || operation === "cancel")
        await api.control(job.id, operation);
      else if (operation === "resume") await api.resume(job.id);
      else await api.retry(job.id);
      onAction();
    } finally {
      setBusy(false);
    }
  };

  const extendTime = async (minutes: number | null, removeLimit = false) => {
    setBusy(true);
    try {
      await api.extendTime(job.id, minutes, removeLimit);
      setDetail(await api.job(job.id));
    } finally {
      setBusy(false);
    }
  };

  const tasks = detail.plan?.tasks ?? [];
  const completed = tasks.filter((task) => task.status === "completed").length;
  const currentEvent = events.at(-1);
  const snapshot = detail.progress_snapshot;
  const remainingBudget =
    detail.time_limit_minutes === null
      ? null
      : Math.max(detail.time_limit_minutes * 60 - detail.active_elapsed_seconds, 0);
  const displayedResult = detail.result ?? detail.provisional_result;
  return (
    <div className="stack stack--large">
      <section className="panel run-head">
        <div>
          <div className="eyebrow">ÇALIŞMA DETAYI</div>
          <div className="hero__title-row">
            <h2>{detail.name}</h2>
            <StatusBadge status={detail.status} />
          </div>
          <p>{detail.current_task ?? "İşlem bekleniyor"}</p>
        </div>
        <div className="run-actions">
          {detail.status === "running" && (
            <>
              <button
                className="button button--quiet"
                disabled={busy}
                onClick={() => action("pause")}
              >
                Duraklat
              </button>
              <button
                className="button button--danger"
                disabled={busy}
                onClick={() => action("cancel")}
              >
                Durdur
              </button>
            </>
          )}
          {detail.status === "paused" && (
            <button
              className="button button--primary"
              disabled={busy}
              onClick={() => action("resume")}
            >
              Devam et
            </button>
          )}
          {detail.status === "failed" && (
            <button
              className="button button--primary"
              disabled={busy}
              onClick={() =>
                diagnostics?.failed_tasks.some((task) =>
                  task.root_error.includes("requires unavailable evaluators"),
                )
                  ? onNew()
                  : action("retry")
              }
            >
              {diagnostics?.failed_tasks.some((task) =>
                task.root_error.includes("requires unavailable evaluators"),
              )
                ? "Ayarları düzelterek yeni plan"
                : "Başarısızı tekrarla"}
            </button>
          )}
        </div>
      </section>
      {detail.status === "failed" && diagnostics && (
        <section className="panel diagnostics diagnostics--failed">
          <div className="section-head">
            <div>
              <div className="eyebrow">TANILAMA</div>
              <h3>Neden başarısız oldu?</h3>
            </div>
            <span className="badge badge--failed">
              {diagnostics.failed_tasks.length} başarısız task
            </span>
          </div>
          {diagnostics.failed_tasks.map((task) => (
            <div className="failure-card" key={task.id}>
              <div className="failure-card__head">
                <span className="client-logo">{task.client.slice(0, 1).toUpperCase()}</span>
                <div>
                  <strong>{task.id}</strong>
                  <small>Deneme {task.attempt} · {task.shard_id}</small>
                </div>
              </div>
              <div className="root-error">{task.root_error}</div>
              <div className="resolution">
                <span>Çözüm</span>
                <p>{task.resolution}</p>
              </div>
              <details>
                <summary>Stack trace ve stderr’i göster</summary>
                <pre>{task.logs.stderr || task.traceback || "stderr kaydı yok"}</pre>
              </details>
              {task.logs.stdout && (
                <details>
                  <summary>stdout çıktısını göster</summary>
                  <pre>{task.logs.stdout}</pre>
                </details>
              )}
            </div>
          ))}
          <div className="artifact-paths">
            <div>
              <small>EVENT AKIŞI</small>
              <code>{diagnostics.artifacts.events}</code>
            </div>
            <div>
              <small>TASK LOGLARI</small>
              <code>{diagnostics.artifacts.logs}</code>
            </div>
            <div>
              <small>ÇÖZÜMLENMİŞ PLAN</small>
              <code>{diagnostics.artifacts.plan}</code>
            </div>
          </div>
        </section>
      )}
      <div className="grid grid--3">
        <section className="panel focus-stat">
          <small>GENEL İLERLEME</small>
          <strong>%{detail.progress.toFixed(1)}</strong>
          <Meter label={`${completed}/${tasks.length} task`} value={detail.progress} />
        </section>
        <section className="panel focus-stat">
          <small>TAHMİNİ KALAN</small>
          <strong>{formatDuration(detail.eta_seconds)}</strong>
          <p>{detail.eta_confidence} güven</p>
        </section>
        <section className="panel focus-stat">
          <small>AKTİF AŞAMA</small>
          <strong className="phase-name">
            {String(currentEvent?.phase ?? "setup")}
          </strong>
          <p>{String(currentEvent?.status ?? "bekleniyor")}</p>
        </section>
      </div>
      <div className="grid grid--3">
        <section className="panel counter-panel">
          <small>AKTİF TASK</small>
          <strong>
            {snapshot ? `${snapshot.task_index}/${snapshot.task_total}` : "—"}
          </strong>
          <p>
            {snapshot?.client ?? "Client bekleniyor"} ·{" "}
            {snapshot?.shard_id ?? "shard bekleniyor"}
          </p>
          <Meter label="Task ilerlemesi" value={snapshot?.task_progress ?? 0} />
        </section>
        <section className="panel counter-panel">
          <small>SCENARIO / SORU</small>
          <strong>
            {snapshot
              ? `${snapshot.scenario_index}/${snapshot.scenario_total}`
              : "—"}
          </strong>
          <p>
            Soru{" "}
            {snapshot
              ? `${snapshot.question_index}/${snapshot.question_total}`
              : "—"}{" "}
            · Context{" "}
            {snapshot?.context_total
              ? `${snapshot.context_index ?? 0}/${snapshot.context_total}`
              : "—"}
          </p>
        </section>
        <section className="panel counter-panel">
          <small>SÜRE BÜTÇESİ</small>
          <strong>
            {remainingBudget === null ? "Limitsiz" : formatDuration(remainingBudget)}
          </strong>
          <p>Aktif süre {formatDuration(Math.round(detail.active_elapsed_seconds))}</p>
          {detail.pause_reason === "time_limit" && (
            <div className="time-actions">
              <button disabled={busy} onClick={() => extendTime(30)}>
                +30 dk
              </button>
              <button disabled={busy} onClick={() => extendTime(60)}>
                +1 saat
              </button>
              <button disabled={busy} onClick={() => extendTime(null, true)}>
                Limiti kaldır
              </button>
            </div>
          )}
        </section>
      </div>
      <div className="layout-2-1">
        <section className="panel">
          <div className="section-head">
            <div>
              <div className="eyebrow">SHARD HARİTASI</div>
              <h3>Çalışma kapsamı</h3>
            </div>
          </div>
          <div className="task-map">
            {tasks.map((task) => (
              <span
                key={task.id}
                className={`task-dot task-dot--${task.status}`}
                title={`${task.id}: ${task.status}`}
              >
                {task.client.slice(0, 1).toUpperCase()}
              </span>
            ))}
          </div>
        </section>
        <section className="panel event-panel">
          <div className="event-heading">
            <div className="eyebrow">CANLI OLAYLAR</div>
            <span className={`stream-state ${streamConnected ? "online" : ""}`}>
              <i /> SSE {streamConnected ? "bağlı" : "yeniden bağlanıyor"}
            </span>
          </div>
          <div className="event-list">
            {[...events].reverse().slice(0, 12).map((event, index) => (
              <div key={`${String(event.timestamp)}-${index}`}>
                <span className="event-dot" />
                <p>
                  <strong>{String(event.phase)}</strong>
                  {String(event.message || event.status)}
                </p>
              </div>
            ))}
            {!events.length && <p className="muted">Event akışı bekleniyor…</p>}
          </div>
        </section>
      </div>
      {displayedResult && <ResultTable result={displayedResult} />}
      {diagnostics && (
        <section className="panel integrity-panel">
          <div className="section-head">
            <div>
              <div className="eyebrow">SONUÇ SAĞLIĞI</div>
              <h3>Otomatik bütünlük kontrolleri</h3>
            </div>
          </div>
          <div className="integrity-checks">
            {diagnostics.checks.map((check, index) => (
              <div key={`${check.label}-${index}`} className={`check check--${check.status}`}>
                <i>{check.status === "passed" ? "✓" : check.status === "failed" ? "×" : "!"}</i>
                <span><strong>{check.label}</strong><small>{check.detail}</small></span>
              </div>
            ))}
          </div>
        </section>
      )}
      {detail.error && <div className="alert alert--bad">{detail.error}</div>}
    </div>
  );
}

function ResultTable({ result }: { result: ResultSummary }) {
  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [questions, setQuestions] = useState<QuestionRow[]>([]);
  const [loadingQuestions, setLoadingQuestions] = useState(false);

  const inspect = async (client: string) => {
    if (selectedClient === client) {
      setSelectedClient(null);
      setQuestions([]);
      return;
    }
    setSelectedClient(client);
    setLoadingQuestions(true);
    try {
      const response = await api.questions(result.job_id, client);
      setQuestions(response.items);
    } finally {
      setLoadingQuestions(false);
    }
  };

  return (
    <section className="panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">SONUÇ ÖZETİ</div>
          <h3>Client karşılaştırması</h3>
        </div>
        <div className="export-actions">
          <span className={`badge ${result.verified ? "badge--completed" : ""}`}>
            {result.verified ? "Doğrulandı" : "Geçici / Kısmi"}
          </span>
          {(["md", "json", "csv"] as const).map((format) => (
            <a
              key={format}
              className="button button--quiet"
              href={api.exportUrl(result.job_id, format)}
            >
              {format.toUpperCase()} indir
            </a>
          ))}
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Client</th>
              <th>Soru</th>
              <th>Accuracy</th>
              <th>Hit@1</th>
              <th>Hit@5</th>
              <th>MRR</th>
              <th>nDCG</th>
              <th>Token F1</th>
              <th>Retrieval</th>
              <th>Generation</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {Object.entries(result.systems).map(([client, value]) => (
              <tr key={client}>
                <td>
                  <strong>{client}</strong>
                </td>
                <td>{value.questions}</td>
                <td>%{(value.accuracy * 100).toFixed(1)}</td>
                <td>{value.hit_at_1.toFixed(3)}</td>
                <td>{value.hit_at_5.toFixed(3)}</td>
                <td>{value.mrr.toFixed(3)}</td>
                <td>{value.ndcg.toFixed(3)}</td>
                <td>{value.token_f1?.toFixed(3) ?? "N/A"}</td>
                <td>
                  {value.avg_retrieval_ms?.toFixed(1) ?? "N/A"}
                  {value.avg_retrieval_ms !== null && " ms"}
                </td>
                <td>
                  {value.avg_generation_ms?.toFixed(1) ?? "N/A"}
                  {value.avg_generation_ms !== null && " ms"}
                </td>
                <td>
                  <button className="button button--quiet" onClick={() => inspect(client)}>
                    {selectedClient === client ? "Kapat" : "Soruları incele"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedClient && (
        <div className="question-inspector">
          <div className="section-head">
            <div>
              <div className="eyebrow">SORU İNCELEME</div>
              <h3>{selectedClient} · raw sonuçlar</h3>
            </div>
            <span className="badge">{questions.length} kayıt</span>
          </div>
          {loadingQuestions ? (
            <p className="muted">Sorular yükleniyor…</p>
          ) : (
            <div className="question-list">
              {questions.map((row) => (
                <details key={`${row.shard_id}-${row.scenario_id}-${row.question_id}`}>
                  <summary>
                    <span className={row.is_correct ? "answer-good" : "answer-bad"}>
                      {row.is_correct ? "✓" : "×"}
                    </span>
                    <strong>{row.query || row.question_id}</strong>
                    <small>{row.failure_attribution}</small>
                  </summary>
                  <div className="question-detail">
                    <div>
                      <small>GOLDEN CEVAP</small>
                      <p>{row.reference_answers.join(" · ") || row.ground_truth}</p>
                    </div>
                    <div>
                      <small>ÜRETİLEN CEVAP</small>
                      <p>{row.actual_answer || "Boş cevap"}</p>
                    </div>
                    <div className="context-compare">
                      <span>
                        <small>BEKLENEN CONTEXT</small>
                        <code>{row.expected_context_ids.join(", ") || "—"}</code>
                      </span>
                      <span>
                        <small>GETİRİLEN SIRALAMA</small>
                        <code>{row.retrieved_context_ids.join(" → ") || "—"}</code>
                      </span>
                    </div>
                  </div>
                </details>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function OllamaPanel({
  settings,
  onClose,
  onChanged,
}: {
  settings: OllamaSettings | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [url, setUrl] = useState(settings?.url ?? "http://127.0.0.1:11434");
  const [model, setModel] = useState(settings?.model ?? "");
  const [models, setModels] = useState(settings?.models ?? []);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const test = async () => {
    setBusy(true);
    setStatus(null);
    try {
      const response = await api.testOllama(url);
      setModels(response.models);
      if (!model && response.models.length) setModel(response.models[0]);
      setStatus(`${response.models.length} model bulundu; bağlantı çalışıyor.`);
    } catch (reason) {
      setStatus((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setStatus(null);
    try {
      await api.saveOllama(url, model || null);
      await onChanged();
      setStatus("Bağlantı ve varsayılan model kaydedildi.");
    } catch (reason) {
      setStatus((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteOllama();
      await onChanged();
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ollama-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="section-head">
          <div>
            <div className="eyebrow">MODEL SERVİSİ</div>
            <h2 id="ollama-title">Ollama bağlantısı</h2>
          </div>
          <button className="icon-button" aria-label="Kapat" onClick={onClose}>
            ×
          </button>
        </div>
        <p className="lead">
          Yalnız yerel makine veya özel LAN adresleri kabul edilir. Kimlik bilgisi
          saklanmaz.
        </p>
        <label className="field">
          <span>Sunucu URL’si</span>
          <input
            value={url}
            placeholder="http://192.168.1.103:11434"
            onChange={(event) => setUrl(event.target.value)}
          />
          <small className="field-help">
            Örnek: http://127.0.0.1:11434 veya http://192.168.1.103:11434
          </small>
        </label>
        <div className="modal-test-row">
          <button className="button button--quiet" disabled={busy} onClick={test}>
            {busy ? "Kontrol ediliyor…" : "Bağlantıyı test et"}
          </button>
          <span className={`connection ${models.length ? "online" : ""}`}>
            <span />
            {models.length ? `${models.length} model erişilebilir` : "Test bekleniyor"}
          </span>
        </div>
        <label className="field">
          <span>Varsayılan model</span>
          <select value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="">Model seç</option>
            {models.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        {status && <div className="protocol-note"><span>i</span><p>{status}</p></div>}
        <div className="wizard__actions">
          <button className="button button--danger" disabled={busy} onClick={remove}>
            Kaydı kaldır
          </button>
          <div className="inline-actions">
            <button className="button button--quiet" onClick={onClose}>
              Vazgeç
            </button>
            <button className="button button--primary" disabled={busy} onClick={save}>
              Kaydet
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function DatasetsPage({
  catalog,
  onRefresh,
}: {
  catalog: Catalog | null;
  onRefresh: () => Promise<void>;
}) {
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<DatasetInfo | null>(null);
  const [scenarios, setScenarios] = useState<DatasetScenario[]>([]);
  const [scenarioTotal, setScenarioTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState<{
    id: string;
    status: string;
    progress: number;
    error: string | null;
  } | null>(null);

  const visible = (catalog?.datasets ?? []).filter(
    (dataset) => filter === "all" || dataset.group === filter,
  );

  const inspect = async (dataset: DatasetInfo, nextOffset = 0) => {
    setSelected(dataset);
    setBusy(true);
    try {
      if (dataset.ready) {
        const response = await api.datasetScenarios(dataset.id, nextOffset);
        setScenarios(response.items);
        setScenarioTotal(response.total);
        setOffset(nextOffset);
      } else {
        setScenarios([]);
        setScenarioTotal(0);
      }
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!operation || ["completed", "failed"].includes(operation.status)) return;
    const timer = window.setInterval(async () => {
      const next = await api.datasetOperation(operation.id);
      setOperation(next);
      if (next.status === "completed") await onRefresh();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [operation?.id, operation?.status, onRefresh]);

  const sync = async () => {
    if (!selected?.sync_target) return;
    const accepted = window.confirm(
      `${selected.name} pinned kaynaktan indirilecek. Lisans: ${selected.license}. Devam edilsin mi?`,
    );
    if (!accepted) return;
    setBusy(true);
    try {
      const response = await api.syncDataset(selected.id);
      setOperation({ ...response, progress: 0, error: null });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dataset-workspace">
      <section className="panel">
        <div className="section-head">
          <div>
            <div className="eyebrow">KAYNAK KATALOĞU</div>
            <h2>Datasetler</h2>
          </div>
        </div>
        <div className="filter-tabs">
          {[
            ["all", "Tümü"],
            ["internal", "Internal"],
            ["release", "Release"],
            ["research", "Research"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={filter === id ? "active" : ""}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="dataset-catalog">
          {visible.map((dataset) => (
            <button
              key={dataset.id}
              className={selected?.id === dataset.id ? "selected" : ""}
              onClick={() => inspect(dataset)}
            >
              <span className="dataset-icon">◇</span>
              <span>
                <strong>{dataset.name}</strong>
                <small>
                  {dataset.counts.scenarios} scenario · {dataset.counts.questions} soru
                </small>
              </span>
              <i className={dataset.ready ? "ready" : ""}>
                {dataset.ready ? "Hazır" : "Eksik"}
              </i>
            </button>
          ))}
        </div>
      </section>
      <section className="panel dataset-detail">
        {!selected ? (
          <EmptyState
            title="Bir dataset seç"
            body="Amaç, lisans, kapsam ve örnek sorular burada gösterilecek."
          />
        ) : (
          <>
            <div className="section-head">
              <div>
                <div className="eyebrow">{selected.group.toUpperCase()}</div>
                <h2>{selected.name}</h2>
              </div>
              <span className={`badge ${selected.ready ? "badge--completed" : "badge--paused"}`}>
                {selected.ready ? "Hazır" : "Sync gerekli"}
              </span>
            </div>
            <p className="lead">{selected.purpose}</p>
            <div className="dataset-meta-grid">
              <div><small>SÜRÜM</small><strong>{selected.version}</strong></div>
              <div><small>LİSANS</small><strong>{selected.license}</strong></div>
              <div><small>DESIGNATION</small><strong>{selected.designation}</strong></div>
              <div>
                <small>BOYUT</small>
                <strong>
                  {selected.file_size_bytes === null
                    ? selected.estimated_download ?? "Bilinmiyor"
                    : `${(selected.file_size_bytes / 1_048_576).toFixed(1)} MB`}
                </strong>
              </div>
              <div><small>CONTEXT</small><strong>{selected.counts.contexts}</strong></div>
              <div><small>SORU</small><strong>{selected.counts.questions}</strong></div>
            </div>
            <div className="protocol-note">
              <span>i</span>
              <p>
                Önerilen profil: {selected.recommended_profiles.join(", ")} · Checksum{" "}
                {selected.checksum_valid ? "doğrulandı" : "doğrulanmadı"}.
              </p>
            </div>
            <div className="metric-support">
              <div>
                <small>DESTEKLENEN METRİKLER</small>
                <p>{selected.metrics.supported.join(", ") || "Belirtilmemiş"}</p>
              </div>
              <div>
                <small>DESTEKLENMEYEN METRİKLER</small>
                <p>{selected.metrics.unsupported.join(", ") || "Yok"}</p>
              </div>
            </div>
            {!selected.ready && selected.sync_target && (
              <div className="sync-box">
                <div>
                  <strong>Pinned kaynaktan senkronize et</strong>
                  <p>İndirme yalnız açık onayınla başlar; benchmark sırasında ağır sync başlatılmaz.</p>
                </div>
                <button className="button button--primary" disabled={busy} onClick={sync}>
                  Dataseti indir
                </button>
              </div>
            )}
            {operation && (
              <div className="sync-progress">
                <Meter label={`Sync: ${operation.status}`} value={operation.progress} />
                {operation.error && <div className="alert alert--bad">{operation.error}</div>}
              </div>
            )}
            {selected.ready && (
              <>
                <div className="section-head sample-head">
                  <div>
                    <div className="eyebrow">İÇERİK ÖRNEĞİ</div>
                    <h3>Scenario, soru ve contextler</h3>
                  </div>
                  <span className="badge">{scenarioTotal} scenario</span>
                </div>
                <div className="scenario-list">
                  {busy ? (
                    <p className="muted">İçerik okunuyor…</p>
                  ) : (
                    scenarios.map((scenario) => (
                      <details key={scenario.id}>
                        <summary>
                          <strong>{scenario.id}</strong>
                          <small>
                            {scenario.contexts.length} context · {scenario.questions.length} soru
                          </small>
                        </summary>
                        <div className="scenario-content">
                          {scenario.questions.map((question) => (
                            <article key={question.id}>
                              <small>{question.category ?? "uncategorized"}</small>
                              <strong>{question.query}</strong>
                              <p>Golden: {question.reference_answers.join(" · ") || question.ground_truth}</p>
                              <code>
                                Beklenen: {question.supporting_context_ids.join(", ") || "—"}
                              </code>
                            </article>
                          ))}
                          {scenario.contexts.map((context) => (
                            <article key={context.id} className="context-card">
                              <small>{context.id}</small>
                              <p>{context.text}</p>
                            </article>
                          ))}
                        </div>
                      </details>
                    ))
                  )}
                </div>
                <div className="pagination">
                  <button
                    className="button button--quiet"
                    disabled={offset === 0 || busy}
                    onClick={() => inspect(selected, Math.max(0, offset - 10))}
                  >
                    ← Önceki
                  </button>
                  <span>{offset + 1}–{Math.min(offset + 10, scenarioTotal)} / {scenarioTotal}</span>
                  <button
                    className="button button--quiet"
                    disabled={offset + 10 >= scenarioTotal || busy}
                    onClick={() => inspect(selected, offset + 10)}
                  >
                    Sonraki →
                  </button>
                </div>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function GuidePage({ onNew }: { onNew: () => void }) {
  return (
    <div className="stack stack--large guide">
      <section className="panel guide-hero">
        <div>
          <div className="eyebrow">BAŞLANGIÇ REHBERİ</div>
          <h2>Benchmark neyi kanıtlar, neyi kanıtlamaz?</h2>
          <p>
            Aynı dataset ve protokol altında retrieval, cevap doğruluğu, gecikme ve
            hata davranışını ölçer. Mini smoke yalnız sistemin uçtan uca çalıştığını
            gösterir; genel zekâ veya ürün üstünlüğü kanıtı değildir.
          </p>
        </div>
        <button className="button button--primary" onClick={onNew}>
          Yeni plan oluştur
        </button>
      </section>
      <div className="grid grid--3">
        {[
          ["Quality", "Doğrudan ve eşdeğer ingest ile retrieval/QA karşılaştırması."],
          ["Native Memory", "Ürünün gerçek hafıza yolunu ack latency ve time-to-searchable ile ölçer."],
          ["Capacity", "Generation kapalıyken throughput, depolama ve retrieval kapasitesini ölçer."],
        ].map(([title, body]) => (
          <section className="panel guide-card" key={title}>
            <h3>{title}</h3><p>{body}</p>
          </section>
        ))}
      </div>
      <section className="panel">
        <div className="section-head"><div><div className="eyebrow">METRİK SÖZLÜĞÜ</div><h3>Sonuçları nasıl okumalı?</h3></div></div>
        <div className="glossary">
          <div><strong>Hit@K</strong><p>Doğru context ilk K sonuç arasında mı?</p></div>
          <div><strong>MRR</strong><p>İlk doğru context sıralamada ne kadar yukarıda?</p></div>
          <div><strong>nDCG</strong><p>Birden çok doğru contextin sıralama kalitesini ölçer.</p></div>
          <div><strong>Token F1</strong><p>Üretilen cevap ile golden cevabın token örtüşmesi.</p></div>
          <div><strong>Exact Match</strong><p>Normalize edilmiş cevap birebir eşleşiyor mu?</p></div>
          <div><strong>Latency</strong><p>Retrieval ve generation süreleri ayrı raporlanır.</p></div>
        </div>
      </section>
      <section className="panel">
        <div className="section-head"><div><div className="eyebrow">HAZIR AKIŞLAR</div><h3>İhtiyacına göre başlangıç</h3></div></div>
        <div className="workflow-list">
          <article><span>5 dk</span><div><strong>Hızlı smoke</strong><p>Mini Smoke · Quality · MESA + dense-rag + Mem0 · generation kapalı.</p></div></article>
          <article><span>1 saat</span><div><strong>Kontrollü kalite testi</strong><p>Holdout 600 · otomatik shard · 60 dakika limit · geçici sonucu canlı izle.</p></div></article>
          <article><span>∞</span><div><strong>Capacity</strong><p>Büyük dataset · generation/judge kapalı · throughput ve Hit@K odaklı.</p></div></article>
        </div>
        <div className="protocol-note"><span>i</span><p>Geçici sonuçlar çalışma sırasında değişir. Publishable iddia için yeterli örneklem, bağımsız tekrar ve doğrulanmış bundle gerekir.</p></div>
      </section>
    </div>
  );
}

function AmbientBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    type Particle = {
      x: number;
      y: number;
      vx: number;
      vy: number;
      radius: number;
      color: string;
    };

    const colors = ["139,92,246", "59,130,246", "6,182,212"];
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let frame = 0;
    let particles: Particle[] = [];

    const createParticles = () => {
      const count = Math.max(48, Math.min(130, Math.floor((width * height) / 12_000)));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.16,
        vy: (Math.random() - 0.5) * 0.16,
        radius: Math.random() * 1.25 + 0.65,
        color: colors[Math.floor(Math.random() * colors.length)],
      }));
    };

    const draw = () => {
      context.clearRect(0, 0, width, height);
      if (!reducedMotion) {
        particles.forEach((particle) => {
          particle.x += particle.vx;
          particle.y += particle.vy;
          if (particle.x < 0 || particle.x > width) particle.vx *= -1;
          if (particle.y < 0 || particle.y > height) particle.vy *= -1;
        });
      }

      for (let first = 0; first < particles.length; first += 1) {
        for (let second = first + 1; second < particles.length; second += 1) {
          const dx = particles[first].x - particles[second].x;
          const dy = particles[first].y - particles[second].y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          if (distance < 175) {
            context.beginPath();
            context.strokeStyle = `rgba(${particles[first].color},${
              (1 - distance / 175) * 0.42
            })`;
            context.lineWidth = 0.65;
            context.moveTo(particles[first].x, particles[first].y);
            context.lineTo(particles[second].x, particles[second].y);
            context.stroke();
          }
        }
      }

      particles.forEach((particle) => {
        context.beginPath();
        context.fillStyle = `rgba(${particle.color},0.74)`;
        context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
        context.fill();
      });

      if (!reducedMotion) frame = window.requestAnimationFrame(draw);
    };

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      createParticles();
      if (reducedMotion) draw();
    };

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <div className="ambient-background" aria-hidden="true">
      <div className="ambient-background__mesh" />
      <div className="ambient-background__spot" />
      <canvas ref={canvasRef} className="ambient-background__network" />
      <div className="ambient-background__noise" />
    </div>
  );
}

function App() {
  const [view, setView] = useState<View>("overview");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [system, setSystem] = useState<SystemSnapshot | null>(null);
  const [ollama, setOllama] = useState<OllamaSettings | null>(null);
  const [showOllama, setShowOllama] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [connected, setConnected] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [nextCatalog, nextSystem, nextJobs, nextOllama] = await Promise.all([
        api.catalog(),
        api.system(),
        api.jobs(),
        api.ollama(),
      ]);
      setCatalog(nextCatalog);
      setSystem(nextSystem);
      setJobs(nextJobs);
      setOllama(nextOllama);
      setConnected(true);
      if (selectedJob) {
        const updated = nextJobs.find((item) => item.id === selectedJob.id);
        if (updated) setSelectedJob(updated);
      }
    } catch {
      setConnected(false);
    }
  }, [selectedJob?.id]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const openJob = (job: Job) => {
    setSelectedJob(job);
    setView("runs");
  };

  const pageTitle =
    selectedJob && view === "runs"
      ? "Çalışma Detayı"
      : navigation.find((item) => item.id === view)?.label;

  return (
    <>
      <AmbientBackground />
      <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark">M</span>
          <div>
            <strong>MESA</strong>
            <small>Benchmark Console</small>
          </div>
        </div>
        <nav>
          {navigation.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => {
                setView(item.id);
                if (item.id !== "runs") setSelectedJob(null);
              }}
            >
              <span>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar__foot">
          <div className={`connection ${connected ? "online" : ""}`}>
            <span />
            {connected ? "Yerel servis bağlı" : "Bağlantı kesildi"}
          </div>
          <small>v1.0 · local-only</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div>
            <small>MESA / BENCHMARK</small>
            <strong>{pageTitle}</strong>
          </div>
          <div className="topbar__status">
            <button
              className={`ollama-pill ${system?.ollama.online ? "online" : ""}`}
              onClick={() => setShowOllama(true)}
              title="Ollama bağlantısını yönet"
            >
              <i />
              Ollama
              <b>{system?.ollama.model ?? "Bağlı değil"}</b>
            </button>
            <span className="resource-pill">
              CPU <b>%{Math.round(system?.cpu_percent ?? 0)}</b>
            </span>
            <span className="resource-pill">
              RAM <b>%{Math.round(system?.memory_percent ?? 0)}</b>
            </span>
            <button className="avatar" title="Yerel kullanıcı">
              Y
            </button>
          </div>
        </header>
        <main>
          {view === "overview" && (
            <Overview
              jobs={jobs}
              system={system}
              onNew={() => setView("new")}
              onOpen={openJob}
            />
          )}
          {view === "new" && (
            <NewBenchmark
              catalog={catalog}
              ollama={ollama}
              onCreated={(job) => {
                setJobs((current) => [job, ...current]);
                openJob(job);
              }}
            />
          )}
          {view === "runs" &&
            (selectedJob ? (
              <JobDetail
                job={selectedJob}
                onAction={refresh}
                onNew={() => {
                  setSelectedJob(null);
                  setView("new");
                }}
              />
            ) : (
              <section className="panel">
                <div className="section-head">
                  <div>
                    <div className="eyebrow">OPERASYON</div>
                    <h2>Tüm çalışmalar</h2>
                  </div>
                </div>
                <JobTable jobs={jobs} onOpen={openJob} />
              </section>
            ))}
          {view === "results" && (
            <div className="stack stack--large">
              {jobs.filter((job) => job.result).map((job) => (
                <ResultTable key={job.id} result={job.result!} />
              ))}
              {!jobs.some((job) => job.result) && (
                <EmptyState
                  title="Henüz doğrulanmış sonuç yok"
                  body="Tamamlanan benchmark’lar doğrulandıktan sonra burada karşılaştırılır."
                  action={() => setView("new")}
                />
              )}
            </div>
          )}
          {view === "datasets" && (
            <DatasetsPage catalog={catalog} onRefresh={refresh} />
          )}
          {view === "clients" && (
            <section className="panel">
              <div className="section-head">
                <div>
                  <div className="eyebrow">ADAPTER SAĞLIĞI</div>
                  <h2>Client’lar</h2>
                </div>
              </div>
              <div className="catalog-grid">
                {catalog?.clients.map((client) => (
                  <article key={client.id}>
                    <span
                      className={`client-logo ${client.available ? "available" : ""}`}
                    >
                      {client.name.slice(0, 1)}
                    </span>
                    <div>
                      <strong>{client.name}</strong>
                      <p>{client.available ? "Çalışmaya hazır" : client.reason}</p>
                      <small>
                        Quality {client.quality_mode ? "✓" : "—"} · Native{" "}
                        {client.native_mode ? "✓" : "—"}
                      </small>
                    </div>
                    <span className={`health-dot ${client.available ? "online" : ""}`} />
                  </article>
                ))}
              </div>
            </section>
          )}
          {view === "system" && (
            <div className="stack stack--large">
              <div className="grid grid--3">
                <section className="panel focus-stat">
                  <small>CPU</small>
                  <strong>%{Math.round(system?.cpu_percent ?? 0)}</strong>
                  <Meter label="Host kullanımı" value={system?.cpu_percent ?? 0} />
                </section>
                <section className="panel focus-stat">
                  <small>RAM</small>
                  <strong>%{Math.round(system?.memory_percent ?? 0)}</strong>
                  <Meter label="Bellek kullanımı" value={system?.memory_percent ?? 0} />
                </section>
                <section className="panel focus-stat">
                  <small>DİSK</small>
                  <strong>%{Math.round(system?.disk_percent ?? 0)}</strong>
                  <Meter label="Çalışma alanı" value={system?.disk_percent ?? 0} />
                </section>
              </div>
              <section className="panel system-ollama">
                <span className={`big-signal ${system?.ollama.online ? "online" : ""}`} />
                <div>
                  <div className="eyebrow">MODEL SERVİSİ</div>
                  <h2>{system?.ollama.model ?? "Ollama çevrimdışı"}</h2>
                  <p>
                    Sağlık kontrolü{" "}
                    {system?.ollama.latency_ms
                      ? `${system.ollama.latency_ms.toFixed(0)} ms`
                      : "alınamadı"}
                  </p>
                </div>
                <button
                  className="button button--primary"
                  onClick={() => setShowOllama(true)}
                >
                  Bağlantıyı yönet
                </button>
              </section>
            </div>
          )}
          {view === "guide" && <GuidePage onNew={() => setView("new")} />}
        </main>
        </div>
      </div>
      {showOllama && (
        <OllamaPanel
          settings={ollama}
          onClose={() => setShowOllama(false)}
          onChanged={refresh}
        />
      )}
    </>
  );
}

export default App;
