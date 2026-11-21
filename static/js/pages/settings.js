import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const [j, packages, orches] = await Promise.all([
    api('/api/settings'),
    api('/api/paths'),
    api('/api/paths/orchestrations'),
  ]);
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <p class="muted">路径口径：${packages.landing_label}。关键路径包只许走一种工程落点，切换口径前须清空另一落点的路径包。</p>
    </section>
    <div class="split" style="margin-top:1rem">
      <section class="pane">
        <h2>参数清单</h2>
        <div class="wall">
          ${(j.items || []).map((s) => {
            if (s.key === 'path_landing') {
              return `<article class="card card-critical">
                <h3>${s.label}</h3>
                <p class="muted">${s.key} · 现行 ${s.value_label || s.value}</p>
                <div class="row">
                  <select data-k="${s.key}">
                    ${(s.options || []).map((o) => `<option value="${o.value}" ${o.value === s.value ? 'selected' : ''}>${o.label}（${o.value}）</option>`).join('')}
                  </select>
                  <button data-save="${s.key}" class="primary" type="button">保存</button>
                </div>
                <p class="muted">快照 ${packages.counts.snapshot} 条 · 影子草稿 ${packages.counts.shadow} 条</p>
              </article>`;
            }
            return `<article class="card">
              <h3>${s.label}</h3>
              <p class="muted">${s.key}</p>
              <div class="row">
                <input data-k="${s.key}" value="${s.value}" />
                <button data-save="${s.key}" class="primary" type="button">保存</button>
              </div>
            </article>`;
          }).join('')}
        </div>
      </section>
      <section class="pane">
        <section class="card">
          <h2>路径口径说明</h2>
          <p class="muted">不可变快照（snapshot）：标星当下锁死节点序与边指纹，库级触发器禁止改写或删除。</p>
          <p class="muted">影子草稿（shadow）：路径包收在草稿表，供影响分析只读引用。</p>
          <p class="muted">两种落点禁止并存；影响页始终只跟现行口径下的路径包。</p>
          <p class="muted">现行落点下：快照 ${packages.counts.snapshot} 条 · 影子草稿 ${packages.counts.shadow} 条。</p>
        </section>
        <section class="card" style="margin-top:.85rem">
          <h2>续采编排留痕</h2>
          ${(orches.items || []).map((o) => `<p class="${o.status === '已编排' ? 'muted' : 'err'}">
            #${o.id} · ${o.path_key} · 长 ${o.path_length} · <b>${o.status}</b>
          </p>`).join('') || '<p class="muted">暂无续采编排记录</p>'}
        </section>
      </section>
    </div>`;
  view.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const input = view.querySelector('[data-k="' + btn.dataset.save + '"]');
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
        });
        setMsg('参数已保存');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
