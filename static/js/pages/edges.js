import { api } from '../api.js';

function chips(chips) {
  if (!chips || !chips.length) return '<span class="muted">不属关键路径</span>';
  return chips.map((c) => `<span class="tag tag-star">路径来源：${c.landing_label} · ${c.path_key} · 长 ${c.path_length}</span>`).join(' ');
}

function pkgSummary(pkg) {
  const drift = pkg.drift && !pkg.drift.ok;
  return `<article class="card ${drift ? 'card-warn' : ''}">
    <h3>${pkg.path_key}</h3>
    <span class="tag ${drift ? 'tag-warn' : 'tag-star'}">${pkg.landing_label}</span>
    <span class="tag">同源路径长度 ${pkg.path_length}</span>
    <span class="tag">${drift ? '指纹漂移' : '指纹一致'}</span>
    <p class="muted">${pkg.path_rows.map((r) => r.from + ' → ' + r.to).join(' · ')}</p>
  </article>`;
}

export async function renderEdges(view, setMsg) {
  const [nodes, edges, packages] = await Promise.all([
    api('/api/nodes'), api('/api/edges'), api('/api/paths'),
  ]);
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${n.node_code}</option>`).join('');
  view.innerHTML = `
    <section class="card">
      <h2>边关系</h2>
      <p class="muted">当前路径口径：${packages.landing_label}（路径来源芯片只读派生自该口径的路径包）</p>
      <div class="row">
        <label>边号<input id="code" value="E-NEW-1" /></label>
        <label>上游<select id="up">${opts}</select></label>
        <label>下游<select id="down">${opts}</select></label>
        <label>变换<input id="kind" value="直抽" /></label>
        <button class="primary" id="create" type="button">创建边</button>
      </div>
    </section>
    <div class="split" style="margin-top:1rem">
      <section class="pane">
        <h2>依赖列表</h2>
        <div class="wall">
          ${(edges.items || []).map((e) => `<article class="card">
            <h3>${e.edge_code}</h3>
            <span class="tag">${e.status}</span>
            ${e.path_chips && e.path_chips.length ? '<span class="tag tag-star">关键路径</span>' : ''}
            <p>${e.upstream_code} → ${e.downstream_code}</p>
            <p class="muted">${e.transform_kind} · ${e.upstream_table} → ${e.downstream_table}</p>
            <p>${chips(e.path_chips)}</p>
            <div class="row">
              <button data-pub="${e.id}" class="primary" type="button">发布</button>
              <button data-void="${e.id}" type="button">作废</button>
            </div>
          </article>`).join('') || '<p class="muted">暂无边</p>'}
        </div>
      </section>
      <section class="pane">
        <h2>关键路径概览</h2>
        <div class="wall">
          ${(packages.items || []).map(pkgSummary).join('') || '<p class="muted">当前口径暂无路径包</p>'}
        </div>
      </section>
    </div>`;
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/edges', {
        method: 'POST',
        body: JSON.stringify({
          edge_code: document.getElementById('code').value,
          upstream_id: Number(document.getElementById('up').value),
          downstream_id: Number(document.getElementById('down').value),
          transform_kind: document.getElementById('kind').value,
        }),
      });
      setMsg('边已创建');
      await renderEdges(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-pub]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.pub + '/publish', { method: 'POST', body: '{}' });
        setMsg('边已发布');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-void]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.void + '/void', { method: 'POST', body: '{}' });
        setMsg('边已作废');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
