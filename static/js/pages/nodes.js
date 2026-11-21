import { api } from '../api.js';

function pkgRows(pkg) {
  return (pkg.path_rows || []).map((r) => `<tr>
    <td>${r.seq}</td>
    <td>${r.from} → ${r.to}</td>
    <td class="muted">${r.edge_code}</td>
  </tr>`).join('');
}

function pkgCard(pkg) {
  const drift = pkg.drift && !pkg.drift.ok;
  return `<article class="card">
    <h3>${pkg.path_key}</h3>
    <span class="tag ${drift ? 'tag-warn' : ''}">${pkg.landing_label}</span>
    <span class="tag">${drift ? '指纹漂移' : '指纹一致'}</span>
    <span class="tag">路径长度 ${pkg.path_length}</span>
    <p class="muted">标星 ${pkg.starred_by} · ${pkg.starred_at} · 指纹 ${pkg.edge_fingerprint}</p>
    <p class="muted">${pkg.aligned ? '标记、路径长度与表内路径行已对齐' : '路径行与长度未对齐，须核对'}</p>
    <table class="grid">
      <thead><tr><th>序</th><th>路径行</th><th>边</th></tr></thead>
      <tbody>${pkgRows(pkg)}</tbody>
    </table>
    ${drift ? `<p class="err">${pkg.drift.message}（冻结 ${pkg.drift.frozen_fingerprint} / 现行 ${pkg.drift.current_fingerprint}）</p>` : ''}
    <div class="row">
      <button data-orch="${pkg.path_key}" class="primary" type="button">续采编排</button>
    </div>
  </article>`;
}

export async function renderNodes(view, setMsg) {
  const [nodes, packages] = await Promise.all([api('/api/nodes'), api('/api/paths')]);
  const opts = (nodes.items || [])
    .map((n) => `<option value="${n.id}">${n.node_code}</option>`).join('');
  const landingOpts = [
    `<option value="snapshot" ${packages.landing === 'snapshot' ? 'selected' : ''}>不可变快照</option>`,
    `<option value="shadow" ${packages.landing === 'shadow' ? 'selected' : ''}>影子草稿</option>`,
  ].join('');
  view.innerHTML = `
    <section class="card">
      <h2>节点目录</h2>
      <div class="row">
        <label>编号<input id="code" value="N-NEW-1" /></label>
        <label>表名<input id="table" value="dwd_sample" /></label>
        <label>层级<select id="layer"><option>ODS</option><option selected>DWD</option><option>DWS</option><option>ADS</option></select></label>
        <label>责任组<input id="team" value="仓建组" /></label>
        <button class="primary" id="create" type="button">登记节点</button>
      </div>
    </section>
    <div class="split">
      <section class="pane">
        <h2>节点清单</h2>
        <div class="wall">
          ${(nodes.items || []).map((n) => `<article class="card ${n.is_critical ? 'card-critical' : ''}">
            <h3>${n.is_critical ? '★ ' : ''}${n.node_code}</h3>
            <span class="tag">${n.status}</span>
            <span class="tag">${n.layer}</span>
            ${n.is_critical ? `<span class="tag tag-star">关键路径</span>` : ''}
            <p>${n.table_name}</p>
            <p class="muted">上游 ${n.upstream_count} · 下游 ${n.downstream_count} · ${n.owner_team}</p>
            ${n.is_critical ? `<p class="muted">同源路径长度 ${n.path_lengths.join(' / ')} · 所属 ${n.path_marks.map((m) => m.path_key).join('、')}</p>` : ''}
            <div class="row">
              <button data-act="${n.id}" type="button">启用</button>
              <button data-ret="${n.id}" type="button">停用</button>
            </div>
          </article>`).join('') || '<p class="muted">暂无节点</p>'}
        </div>
      </section>
      <section class="pane">
        <section class="card">
          <h2>关键路径标星</h2>
          <p class="muted">当前口径：${packages.landing_label}（路径包只许走一种工程落点）</p>
          <div class="row">
            <label>起点<select id="root">${opts}</select></label>
            <label>终点<select id="sink">${opts}</select></label>
            <label>工程落点<select id="landing">${landingOpts}</select></label>
            <button class="primary" id="star" type="button">标为关键路径</button>
          </div>
        </section>
        <div class="wall" style="margin-top:.85rem">
          ${(packages.items || []).map(pkgCard).join('') || '<p class="muted">当前口径暂无关键路径包</p>'}
        </div>
        ${(packages.archived || []).length ? `<p class="muted" style="margin-top:.85rem">另有 ${packages.archived.length} 条路径包落在非现行口径，禁止并存，影响页不引用。</p>` : ''}
      </section>
    </div>`;

  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          node_code: document.getElementById('code').value,
          table_name: document.getElementById('table').value,
          layer: document.getElementById('layer').value,
          owner_team: document.getElementById('team').value,
        }),
      });
      setMsg('节点已登记');
      await renderNodes(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  document.getElementById('star').onclick = async () => {
    try {
      const r = await api('/api/paths/star', {
        method: 'POST',
        body: JSON.stringify({
          root_node_id: Number(document.getElementById('root').value),
          sink_node_id: Number(document.getElementById('sink').value),
          landing: document.getElementById('landing').value,
        }),
      });
      setMsg(`已标星 ${r.item.path_key}（${r.item.landing_label}），路径长度 ${r.item.path_length}`);
      await renderNodes(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-orch]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/paths/orchestrate', {
          method: 'POST',
          body: JSON.stringify({ path_key: btn.dataset.orch }),
        });
        setMsg(r.message);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-act]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.act + '/activate', { method: 'POST', body: '{}' });
        setMsg('已启用');
        await renderNodes(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-ret]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.ret + '/retire', { method: 'POST', body: '{}' });
        setMsg('已停用');
        await renderNodes(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
