import { api } from '../api.js';

function sourceChip(j) {
  if (!j.path_source || j.path_source === '全图') {
    return '<span class="tag">路径来源：全图</span>';
  }
  return `<span class="tag tag-star">路径来源：${j.path_source}</span>
    <span class="tag">同源路径长度 ${j.path_length}</span>
    ${j.path_drift ? '<span class="tag tag-warn">指纹漂移</span>' : ''}`;
}

function packageBlock(pkg) {
  if (!pkg) return '';
  const drift = pkg.drift && !pkg.drift.ok;
  return `<div class="pkg">
    <p class="muted">路径包 ${pkg.path_key} · ${pkg.landing_label} · 指纹 ${pkg.edge_fingerprint}</p>
    <table class="grid">
      <thead><tr><th>序</th><th>路径行</th><th>边</th></tr></thead>
      <tbody>${(pkg.path_rows || []).map((r) => `<tr><td>${r.seq}</td><td>${r.from} → ${r.to}</td><td class="muted">${r.edge_code}</td></tr>`).join('')}</tbody>
    </table>
    ${drift ? `<p class="err">${pkg.drift.message}</p>` : ''}
  </div>`;
}

function hitsTable(hits) {
  return `<table class="grid">
    <thead><tr><th>跳数</th><th>节点</th><th>层级</th><th>表名</th></tr></thead>
    <tbody>${hits.map((h) => `<tr><td>${h.hop}</td><td>${h.node_code}</td><td>${h.layer}</td><td class="muted">${h.table_name}</td></tr>`).join('')}</tbody>
  </table>`;
}

export async function renderImpact(view, setMsg) {
  const [nodes, jobs, packages, orches] = await Promise.all([
    api('/api/nodes'),
    api('/api/impact/jobs'),
    api('/api/paths'),
    api('/api/paths/orchestrations'),
  ]);
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${n.node_code}</option>`).join('');
  const rootsWithPath = new Set((packages.items || []).map((p) => p.root_node_id));
  view.innerHTML = `
    <section class="card">
      <h2>影响分析</h2>
      <p class="muted">当前路径口径：${packages.landing_label}。根节点有路径包时只沿冻结路径开跑，否则走全图。</p>
      <div class="row">
        <label>根节点<select id="root">${opts}</select></label>
        <button class="primary" id="go" type="button">启动影响分析</button>
      </div>
      <p class="muted" id="hint"></p>
    </section>
    <div class="split" style="margin-top:1rem">
      <section class="pane">
        <h2>作业列表</h2>
        <div class="wall">
          ${(jobs.items || []).map((j) => `<article class="card ${j.path_source !== '全图' ? 'card-critical' : ''}">
            <h3>${j.job_code}</h3>
            <span class="tag">${j.status}</span>
            ${sourceChip(j)}
            <p>根 ${j.node_code} · 命中 ${j.affected_count}</p>
            <p class="muted">${j.message}</p>
            <button data-d="${j.id}" type="button">详情</button>
            <div class="muted" id="j${j.id}"></div>
          </article>`).join('') || '<p class="muted">尚无影响作业</p>'}
        </div>
      </section>
      <section class="pane">
        <section class="card">
          <h2>分析详情</h2>
          <p class="muted">点作业“详情”查看路径来源、同源路径长度、路径行与命中节点。</p>
          <div id="detail" class="muted">尚未选择作业</div>
        </section>
        <section class="card" style="margin-top:.85rem">
          <h2>续采编排</h2>
          <p class="muted">路径指纹跟现行边拧了就拦住续采编排。</p>
          ${(orches.items || []).map((o) => `<p class="${o.status === '已编排' ? 'muted' : 'err'}">
            #${o.id} · ${o.path_key} · ${o.path_source} · 长 ${o.path_length} · <b>${o.status}</b> · ${o.message}
          </p>`).join('') || '<p class="muted">暂无续采编排记录</p>'}
        </section>
      </section>
    </div>`;
  const rootSel = document.getElementById('root');
  const hint = document.getElementById('hint');
  const syncHint = () => {
    const rid = Number(rootSel.value);
    hint.textContent = rootsWithPath.has(rid)
      ? '该根节点有冻结路径包，本次将只沿关键路径展开'
      : '该根节点无路径包，本次走全图已发布边';
  };
  rootSel.onchange = syncHint;
  syncHint();
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/impact/start', {
        method: 'POST',
        body: JSON.stringify({ root_node_id: Number(rootSel.value) }),
      });
      setMsg('影响作业 ' + r.job.job_code + ' · ' + r.job.status + ' · 路径来源 ' + r.job.path_source);
      await renderImpact(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-d]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/impact/jobs/' + btn.dataset.d);
        document.getElementById('detail').innerHTML = `
          <p>${r.job.job_code} · 根 ${r.job.node_code} · 命中 ${r.job.affected_count}</p>
          <p>${sourceChip(r.job)}</p>
          ${packageBlock(r.package)}
          <h3 style="margin-top:.6rem">命中节点</h3>
          ${hitsTable(r.hits || [])}`;
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
