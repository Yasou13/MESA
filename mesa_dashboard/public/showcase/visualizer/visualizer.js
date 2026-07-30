document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('ingestion-form');
    const inputField = document.getElementById('memory-input');
    const submitBtn = document.getElementById('submit-btn');
    const graphOverlayText = document.getElementById('graph-overlay-text');
    
    // Pipeline Steps
    const stepValence = document.getElementById('step-valence');
    const stepExtraction = document.getElementById('step-extraction');
    const stepConsensus = document.getElementById('step-consensus');

    // Canvas setup with HiDPI support
    const canvas = document.getElementById('graphCanvas');
    const ctx = canvas.getContext('2d');

    // Polyfill for roundRect (older browsers)
    if (!ctx.roundRect) {
        CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
            if (typeof r === 'number') r = [r, r, r, r];
            this.moveTo(x + r[0], y);
            this.lineTo(x + w - r[1], y);
            this.arcTo(x + w, y, x + w, y + r[1], r[1]);
            this.lineTo(x + w, y + h - r[2]);
            this.arcTo(x + w, y + h, x + w - r[2], y + h, r[2]);
            this.lineTo(x + r[3], y + h);
            this.arcTo(x, y + h, x, y + h - r[3], r[3]);
            this.lineTo(x, y + r[0]);
            this.arcTo(x, y, x + r[0], y, r[0]);
        };
    }
    let dpr = window.devicePixelRatio || 1;
    
    function resizeCanvas() {
        dpr = window.devicePixelRatio || 1;
        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        canvas.style.width = rect.width + 'px';
        canvas.style.height = rect.height + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    let graphData = { nodes: [], edges: [] };
    let animationFrameId;
    let animStartTime = 0;

    // Detect if running inside an iframe — compact hero if so
    if (window.self !== window.top) {
        const hero = document.querySelector('.hero');
        if (hero) {
            hero.style.padding = '1.5rem 2rem 0.75rem';
        }
        const heroH1 = document.querySelector('.hero h1');
        if (heroH1) heroH1.style.fontSize = '2rem';
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const text = inputField.value.trim();
        if (!text) return;

        // Reset state
        resetPipeline();
        submitBtn.disabled = true;
        submitBtn.querySelector('span').innerText = 'İşleniyor...';
        graphOverlayText.style.opacity = '0';
        
        // Stop any existing animation
        if (animationFrameId) cancelAnimationFrame(animationFrameId);
        ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);

        try {
            // Simulate Pipeline Step 1: Valence
            const ecodScore = (Math.random() * 0.08 + 0.01).toFixed(3);
            await runStep(stepValence, 'Analiz Ediliyor...', 1200, `Geçti (ECOD: ${ecodScore})`);

            // Simulate Pipeline Step 2: Extraction — generate triplets
            const triplets = extractTriplets(text);
            const tripletCount = triplets.length;
            await runStep(stepExtraction, 'Triplet Çıkarılıyor...', 1800, `${tripletCount} Triplet Bulundu`);

            // Simulate Pipeline Step 3: Consensus
            await runStep(stepConsensus, 'Çapraz Doğrulama...', 1500, 'Konsensüs Sağlandı ✓');

            // Build the graph from extracted triplets
            buildGraph(triplets);
            
            // Start Graph Animation
            animStartTime = Date.now();
            animateGraph();
            
        } catch (err) {
            console.error(err);
        } finally {
            submitBtn.disabled = false;
            submitBtn.querySelector('span').innerText = 'Veriyi İşle';
        }
    });

    function resetPipeline() {
        const steps = [stepValence, stepExtraction, stepConsensus];
        steps.forEach(step => {
            step.className = 'step';
            step.querySelector('.status-indicator').innerText = 'Bekliyor';
        });
    }

    function runStep(stepElement, activeText, duration, completeText) {
        return new Promise(resolve => {
            stepElement.classList.add('active');
            stepElement.querySelector('.status-indicator').innerText = activeText;
            
            setTimeout(() => {
                stepElement.classList.remove('active');
                stepElement.classList.add('completed');
                stepElement.querySelector('.status-indicator').innerText = completeText;
                resolve();
            }, duration);
        });
    }

    // ─── Intelligent Triplet Extraction (Mock REBEL) ───
    // Splits a complex sentence into meaningful Subject-Predicate-Object triplets
    // by detecting entity-like tokens and common Turkish/English relation patterns.
    
    const STOP_WORDS = new Set([
        // Turkish
        'bir', 'bu', 'şu', 've', 'ile', 'için', 'de', 'da', 'den', 'dan',
        'gibi', 'ise', 'hem', 'ama', 'veya', 'ya', 'ki', 'daha', 'en',
        'olan', 'olan', 'olarak', 'aynı', 'zamanda', 'oranında',
        // English
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to',
        'and', 'for', 'on', 'at', 'by', 'with', 'that', 'this', 'from',
        'its', 'has', 'had', 'have', 'will', 'also', 'as', 'up'
    ]);

    function extractTriplets(text) {
        // Clean and tokenize
        const cleaned = text.replace(/["""''`]/g, '').replace(/[,;()]/g, ' ');
        const tokens = cleaned.split(/\s+/).filter(w => w.length > 0);
        
        // Extract entity candidates: words that start with uppercase OR contain digits OR are longer than 4 chars and not stop words
        const entities = [];
        let currentEntity = [];
        
        for (let i = 0; i < tokens.length; i++) {
            const w = tokens[i];
            const isEntityLike = (
                /^[A-ZÇĞİÖŞÜ]/.test(w) ||               // Starts with uppercase
                /\d/.test(w) ||                             // Contains digits (e.g. Q4, 2025, %35)
                /[-']/.test(w)                              // Compound words (Sycamore-X, CEO'su)
            );
            
            if (isEntityLike) {
                currentEntity.push(w);
            } else {
                if (currentEntity.length > 0) {
                    entities.push(currentEntity.join(' '));
                    currentEntity = [];
                }
                // Also add long meaningful words as entities
                if (w.length > 5 && !STOP_WORDS.has(w.toLowerCase())) {
                    entities.push(w);
                }
            }
        }
        if (currentEntity.length > 0) {
            entities.push(currentEntity.join(' '));
        }

        // Deduplicate entities
        const uniqueEntities = [...new Set(entities)].slice(0, 8); // Cap at 8 for readability
        
        if (uniqueEntities.length < 2) {
            // Fallback: just take the longest words
            const fallback = tokens
                .filter(w => w.length > 3 && !STOP_WORDS.has(w.toLowerCase()))
                .slice(0, 4);
            if (fallback.length >= 2) {
                return [
                    { subject: fallback[0], predicate: 'İLİŞKİLİDİR', object: fallback[1] },
                    ...(fallback[2] ? [{ subject: fallback[0], predicate: 'SAHİPTİR', object: fallback[2] }] : [])
                ];
            }
            return [{ subject: tokens[0] || 'Özne', predicate: 'İLİŞKİLİDİR', object: tokens[1] || 'Nesne' }];
        }

        // Build triplets: first entity is the central subject, connect to others
        const predicates = [
            'SAHİPTİR', 'İLİŞKİLİDİR', 'PARÇASIDIR', 'ETKİLER',
            'ÜRETİR', 'BAĞLIDIR', 'İÇERİR', 'DOĞRULAR'
        ];
        
        const triplets = [];
        const mainSubject = uniqueEntities[0];
        
        for (let i = 1; i < uniqueEntities.length; i++) {
            triplets.push({
                subject: mainSubject,
                predicate: predicates[(i - 1) % predicates.length],
                object: uniqueEntities[i]
            });
        }
        
        // Add a cross-link between secondary entities if we have enough
        if (uniqueEntities.length >= 4) {
            triplets.push({
                subject: uniqueEntities[2],
                predicate: 'DOĞRULAR',
                object: uniqueEntities[3]
            });
        }

        return triplets;
    }

    // ─── Graph Builder ───
    
    const NODE_COLORS = ['#ec4899', '#6366f1', '#10b981', '#f59e0b', '#06b6d4', '#ef4444', '#8b5cf6', '#14b8a6'];

    function buildGraph(triplets) {
        const nodeMap = new Map();
        const edges = [];
        const W = canvas.width / dpr;
        const H = canvas.height / dpr;
        const cx = W / 2;
        const cy = H / 2;

        // Collect unique nodes
        triplets.forEach(t => {
            if (!nodeMap.has(t.subject)) nodeMap.set(t.subject, null);
            if (!nodeMap.has(t.object))  nodeMap.set(t.object, null);
        });

        // Position nodes in a circle
        const nodeNames = [...nodeMap.keys()];
        const count = nodeNames.length;
        const radius = Math.min(W, H) * 0.32;

        nodeNames.forEach((name, i) => {
            const angle = (2 * Math.PI * i / count) - Math.PI / 2;
            nodeMap.set(name, {
                id: name,
                x: cx + radius * Math.cos(angle),
                y: cy + radius * Math.sin(angle),
                baseX: cx + radius * Math.cos(angle),
                baseY: cy + radius * Math.sin(angle),
                color: NODE_COLORS[i % NODE_COLORS.length],
                isMain: i === 0
            });
        });

        // Build edges
        triplets.forEach(t => {
            const srcIdx = nodeNames.indexOf(t.subject);
            const tgtIdx = nodeNames.indexOf(t.object);
            if (srcIdx !== -1 && tgtIdx !== -1) {
                edges.push({ source: srcIdx, target: tgtIdx, label: t.predicate });
            }
        });

        graphData = { nodes: [...nodeMap.values()], edges };
    }

    // ─── Graph Renderer ───

    function drawGraph() {
        const W = canvas.width / dpr;
        const H = canvas.height / dpr;
        const elapsed = (Date.now() - animStartTime) / 1000;
        
        ctx.clearRect(0, 0, W, H);
        
        // Gentle floating around base position (bounded, no drift)
        graphData.nodes.forEach((node, i) => {
            node.x = node.baseX + Math.sin(elapsed * 0.8 + i * 1.7) * 4;
            node.y = node.baseY + Math.cos(elapsed * 0.6 + i * 2.3) * 3;
        });

        // Draw Edges
        graphData.edges.forEach(edge => {
            const source = graphData.nodes[edge.source];
            const target = graphData.nodes[edge.target];
            if (!source || !target) return;
            
            // Gradient line
            const grad = ctx.createLinearGradient(source.x, source.y, target.x, target.y);
            grad.addColorStop(0, source.color + '66');
            grad.addColorStop(1, target.color + '66');
            
            ctx.beginPath();
            ctx.moveTo(source.x, source.y);
            ctx.lineTo(target.x, target.y);
            ctx.strokeStyle = grad;
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // Animated data particle along edge
            const particleT = ((elapsed * 0.3 + edge.source * 0.5) % 1);
            const px = source.x + (target.x - source.x) * particleT;
            const py = source.y + (target.y - source.y) * particleT;
            ctx.beginPath();
            ctx.arc(px, py, 2.5, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff88';
            ctx.fill();

            // Label at midpoint with dynamic width
            const midX = (source.x + target.x) / 2;
            const midY = (source.y + target.y) / 2;
            
            ctx.font = '10px JetBrains Mono, monospace';
            const labelWidth = ctx.measureText(edge.label).width + 12;
            
            ctx.fillStyle = 'rgba(10, 10, 14, 0.85)';
            ctx.beginPath();
            ctx.roundRect(midX - labelWidth / 2, midY - 10, labelWidth, 20, 4);
            ctx.fill();
            ctx.strokeStyle = 'rgba(255,255,255,0.15)';
            ctx.lineWidth = 0.5;
            ctx.stroke();
            
            ctx.fillStyle = '#c4b5fd';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(edge.label, midX, midY);
        });

        // Draw Nodes
        graphData.nodes.forEach(node => {
            const nodeRadius = node.isMain ? 10 : 7;
            const glowRadius = node.isMain ? 30 : 22;
            
            // Outer glow
            const gradient = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, glowRadius);
            gradient.addColorStop(0, node.color + '55');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.beginPath();
            ctx.arc(node.x, node.y, glowRadius, 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();

            // Core circle
            ctx.beginPath();
            ctx.arc(node.x, node.y, nodeRadius, 0, Math.PI * 2);
            ctx.fillStyle = node.color;
            ctx.fill();
            ctx.strokeStyle = '#ffffff44';
            ctx.lineWidth = 1;
            ctx.stroke();

            // Inner white dot
            ctx.beginPath();
            ctx.arc(node.x, node.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.fill();

            // Label below node
            const labelText = node.id.length > 18 ? node.id.substring(0, 16) + '…' : node.id;
            ctx.font = `${node.isMain ? 'bold ' : ''}11px Inter, sans-serif`;
            ctx.fillStyle = '#f0f0f5';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText(labelText, node.x, node.y + glowRadius + 4);
        });
    }

    function animateGraph() {
        drawGraph();
        animationFrameId = requestAnimationFrame(animateGraph);
    }
});
