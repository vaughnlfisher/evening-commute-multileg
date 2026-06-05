// Evening Commute Multileg Card v1.0.0
// 3-leg return: CTK->Farringdon (Thameslink) -> Farringdon->Paddington (Elizabeth) -> Paddington->Twyford (GWR/Lizzie)
// Anchored nesting: each leg shows connections catchable after the previous leg arrives.

const VER = '1.0.0';

function statusColor(status, delay) {
  if (!status) return '#9e9e9e';
  const s = status.toLowerCase();
  if (s === 'cancelled') return '#d32f2f';
  if (s === 'delayed' || (delay && delay >= 10)) return '#f44336';
  if (delay && delay >= 3) return '#ff9800';
  return '#4caf50';
}
function statusLabel(status, delay) {
  if (!status) return '';
  if (status.toLowerCase() === 'cancelled') return '\u2715 Cancelled';
  if (delay && delay > 0) return `+${delay}m`;
  return '\u2713 On time';
}

class EveningCommuteMultilegCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
  }
  static getStubConfig() {
    return { entity: 'sensor.evening_commute_summary', title: 'Evening Commute' };
  }
  setConfig(config) {
    if (!config.entity) throw new Error('entity is required');
    this._config = {
      title: 'Evening Commute',
      show_header: true,
      show_last_updated: true,
      ...config,
    };
  }
  set hass(h) { this._hass = h; this._render(); }
  getCardSize() { return 8; }

  _summary() {
    const s = this._hass.states[this._config.entity];
    return s ? s.attributes : null;
  }

  _styles() {
    return `
      :host{display:block}
      ha-card{overflow:hidden;font-family:var(--paper-font-body1_-_font-family,'Roboto',sans-serif);font-size:14px}
      .hdr{display:flex;align-items:center;padding:12px 16px 8px;border-bottom:1px solid var(--divider-color,#e0e0e0);gap:10px}
      .hdr-title{font-size:15px;font-weight:600;color:var(--primary-text-color)}
      .hdr-route{font-size:11px;color:var(--secondary-text-color);margin-top:1px}
      .train-block{border-bottom:2px solid var(--divider-color,rgba(0,0,0,.12))}
      .train-block:last-of-type{border-bottom:none}
      .leg-bar{display:flex;align-items:center;gap:6px;padding:3px 16px;font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--secondary-text-color);background:var(--secondary-background-color,#f5f5f5)}
      .leg-pill{border-radius:10px;padding:1px 7px;font-size:9px;font-weight:800;color:#fff}
      .p1{background:#E1251B}   /* Thameslink magenta-red */
      .p2{background:#9364CC}   /* Elizabeth line purple */
      .p3{background:#0A493E}   /* GWR dark green */
      .row{padding:8px 16px}
      .row .top{display:flex;align-items:baseline;justify-content:space-between;gap:6px}
      .time{font-size:1.2em;font-weight:700;color:var(--primary-text-color);flex-shrink:0}
      .meta{display:flex;align-items:center;gap:8px;flex:1;flex-wrap:wrap;font-size:.8em;color:var(--secondary-text-color)}
      .plat{background:var(--secondary-background-color,#f0f0f0);border-radius:4px;padding:1px 6px}
      .status{font-size:.8em;font-weight:600;flex-shrink:0}
      .sub{font-size:.78em;color:var(--secondary-text-color);margin-top:2px}
      .interchange{display:flex;align-items:center;gap:8px;padding:4px 16px;font-size:.72em;color:var(--secondary-text-color);font-style:italic}
      .interchange .line{flex:1;border-top:1px dashed var(--divider-color,rgba(0,0,0,.2))}
      .l2-wrap{margin-left:14px;border-left:3px solid #9364CC;padding-left:0}
      .l3-wrap{margin-left:14px;border-left:3px solid #0A493E;padding-left:0}
      .l2-row{padding:6px 16px}
      .l3-row{padding:5px 16px;font-size:.95em}
      .none{padding:6px 16px;font-size:.76em;color:var(--secondary-text-color);font-style:italic}
      .footer{padding:5px 16px;font-size:.74em;color:var(--secondary-text-color);border-top:1px solid var(--divider-color,rgba(0,0,0,.08));display:flex;justify-content:space-between}
      .no-trains{padding:18px 16px;text-align:center;color:var(--secondary-text-color)}
    `;
  }

  _row(item, cls, platLabel) {
    const color = statusColor(item.status, item.delay_minutes);
    const lbl = statusLabel(item.status, item.delay_minutes);
    const plat = item.platform ? `<span class="plat">Plat ${item.platform}</span>` : '';
    const waitTxt = (item.wait_mins !== null && item.wait_mins !== undefined)
      ? `${item.wait_mins}m wait` : '';
    return `<div class="row ${cls}">
      <div class="top">
        <span class="time" style="color:${color}">${item.time}</span>
        <div class="meta">${plat}${waitTxt ? `<span>${waitTxt}</span>` : ''}</div>
        <span class="status" style="color:${color}">${lbl}</span>
      </div>
      <div class="sub">Towards ${item.destination}</div>
    </div>`;
  }

  _render() {
    if (!this._hass || !this._config.entity) return;
    const s = this._summary();
    const cfg = this._config;
    const trains = (s && Array.isArray(s.trains)) ? s.trains : [];
    const lastUpdated = s?.last_updated
      ? new Date(s.last_updated).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
      : null;
    const fInt = s?.farringdon_interchange_mins ?? 5;
    const pInt = s?.paddington_interchange_mins ?? 8;

    const hdr = cfg.show_header
      ? `<div class="hdr"><span style="font-size:20px">\ud83c\udf06</span><div><div class="hdr-title">${cfg.title}</div><div class="hdr-route">City Thameslink \u2192 Farringdon \u2192 Paddington \u2192 Twyford</div></div></div>`
      : '';

    let blocks;
    if (!trains.length) {
      blocks = '<div class="no-trains">No services found</div>';
    } else {
      blocks = trains.map(t => {
        const leg1 = `<div class="leg-bar"><span class="leg-pill p1">LEG 1</span>City Thameslink \u2192 Farringdon \u00b7 Thameslink</div>${this._row(t, 'row')}`;

        const leg2list = Array.isArray(t.leg2) ? t.leg2 : [];
        let leg2html;
        if (!leg2list.length) {
          leg2html = `<div class="interchange"><span class="line"></span>\ud83d\udeb6 ${fInt}m interchange<span class="line"></span></div><div class="l2-wrap"><div class="none">No onward Elizabeth line connection yet</div></div>`;
        } else {
          leg2html = `<div class="interchange"><span class="line"></span>\ud83d\udeb6 ${fInt}m interchange at Farringdon<span class="line"></span></div>`
            + `<div class="leg-bar"><span class="leg-pill p2">LEG 2</span>Farringdon \u2192 Paddington \u00b7 Elizabeth line</div>`
            + `<div class="l2-wrap">` + leg2list.map(l2 => {
                const l2row = this._row(l2, 'l2-row');
                const leg3list = Array.isArray(l2.leg3) ? l2.leg3 : [];
                let leg3html;
                if (!leg3list.length) {
                  leg3html = `<div class="interchange"><span class="line"></span>\ud83d\udeb6 ${pInt}m at Paddington<span class="line"></span></div><div class="l3-wrap"><div class="none">No onward Twyford service yet</div></div>`;
                } else {
                  leg3html = `<div class="interchange"><span class="line"></span>\ud83d\udeb6 ${pInt}m interchange at Paddington<span class="line"></span></div>`
                    + `<div class="leg-bar"><span class="leg-pill p3">LEG 3</span>Paddington \u2192 Twyford \u00b7 GWR / Elizabeth</div>`
                    + `<div class="l3-wrap">` + leg3list.map(l3 => this._row(l3, 'l3-row')).join('') + `</div>`;
                }
                return l2row + leg3html;
              }).join('') + `</div>`;
        }
        return `<div class="train-block">${leg1}${leg2html}</div>`;
      }).join('');
    }

    const footer = cfg.show_last_updated && lastUpdated
      ? `<div class="footer"><span>Last updated: ${lastUpdated}</span><span>\ud83c\udf19</span></div>`
      : '';

    this.shadowRoot.innerHTML = `<style>${this._styles()}</style><ha-card>${hdr}${blocks}${footer}</ha-card>`;
  }
}

customElements.define('evening-commute-multileg-card', EveningCommuteMultilegCard);
window.customCards = (window.customCards || []).filter(c => c.type !== 'evening-commute-multileg-card');
window.customCards.push({
  type: 'evening-commute-multileg-card',
  name: 'Evening Commute Multileg Card',
  description: 'CTK->Farringdon->Paddington->Twyford return journey, 3-level anchored nesting',
  preview: true,
});
console.info(`%c EVENING-COMMUTE-MULTILEG-CARD %c v${VER} `, 'background:#0A493E;color:#fff;font-weight:700;padding:2px 4px;border-radius:3px 0 0 3px', 'background:#9364CC;color:#fff;font-weight:700;padding:2px 4px;border-radius:0 3px 3px 0');
