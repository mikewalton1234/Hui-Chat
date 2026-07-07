#!/usr/bin/env python3
"""admin_panel_inject.py

Server-side injection for the *admin-only* control panel.

Design goals:
  - Nothing in static end-user assets (chat.html / chat_parts / chat.css) needs to contain admin UI.
  - Admin UI is delivered only when the server is rendering /chat for an admin user.
  - Admin UI calls existing /admin/* endpoints (RBAC-protected) using fetch(..., credentials:'include').

Security notes:
  - The HTML/JS/CSS is injected only when users.is_admin is TRUE.
  - All privileged actions still require valid access JWT + RBAC permission checks.
  - The panel auto-refreshes access tokens via /token/refresh if a request returns 401.
"""

from __future__ import annotations

from html import escape
import json

from registration_name_policy import USERNAME_MIN_LENGTH, USERNAME_MAX_LENGTH, USERNAME_HTML_PATTERN, username_policy_title
from account_creation_policy import password_policy_metadata

def build_admin_injection_snippet(csp_nonce: str | None = None) -> str:
    """Return a single HTML snippet containing the admin panel CSS + JS."""

    css = r"""

/* Echo-Chat Admin Panel — injected (admin only) */
/* v6: UI08 deep recheck — CSRF retry freshness, modal accessibility, keyboard tabs, and broader duplicate-action guards */
/* v8: UI08 admin reauth deep recheck — session confirmation race guard loaded */

#ecAdminPanel{
  /* Readability-first admin palette. Keep the dark style, but avoid muddy dark-on-dark text. */
  --ecap-bg: rgba(13,17,24,.96);
  --ecap-bg2: rgba(24,31,43,.98);
  --ecap-surface: rgba(30,38,51,.94);
  --ecap-surface2: rgba(39,49,66,.96);
  --ecap-border: rgba(226,238,255,.24);
  --ecap-border2: rgba(226,238,255,.38);
  --ecap-text: #f8fbff;
  --ecap-muted: rgba(232,240,255,.86);
  --ecap-faint: rgba(232,240,255,.72);
  --ecap-accent: rgba(142,195,255,1);
  --ecap-danger: rgba(255,84,84,1);
  --ecap-warn: rgba(255,199,107,1);
  --ecap-ok: rgba(120,255,170,1);

  position:fixed;
  top:16px; right:16px;
  width:680px;
  min-width:360px;
  min-height:360px;
  max-width:calc(100vw - 32px);
  max-height:calc(100vh - 32px);
  resize:both;

  background: linear-gradient(180deg, var(--ecap-bg2), var(--ecap-bg));
  color:var(--ecap-text);

  border:1px solid var(--ecap-border);
  border-radius:16px;
  box-shadow: 0 18px 55px rgba(0,0,0,.55);

  font: 14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;
  z-index:999999;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);

  overflow:hidden;
}

/* Hidden (persisted “closed”) state — can be toggled back with a hotkey */
#ecAdminPanel.ecap-hidden{ display:none !important; }

/* Startup auth gate: until the admin password is confirmed, the panel body is
   visually locked and non-interactive.  The password modal remains usable. */
#ecAdminPanel.ecap-startup-locked .ecap-body{
  pointer-events:none;
  user-select:none;
  filter: blur(2px);
  opacity:.22;
}
#ecAdminPanel.ecap-startup-locked .ecap-headBtns .ecap-iconBtn:not(.danger){
  pointer-events:none;
  opacity:.45;
}

#ecAdminPanel *{ box-sizing:border-box; }
#ecAdminPanel ::selection{ background: rgba(124,179,255,.25); }

#ecAdminPanel .ecap-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:10px 10px 8px 12px;
  user-select:none;
  border-bottom:1px solid rgba(226,238,255,.20);
  cursor:move;
}

#ecAdminPanel.ecap-pinned .ecap-head{ cursor:default; }

#ecAdminPanel .ecap-titleRow{ display:flex; align-items:center; gap:10px; min-width:0; }
#ecAdminPanel .ecap-dot{
  width:9px; height:9px; border-radius:999px;
  background: rgba(255,255,255,.25);
  box-shadow: 0 0 0 2px rgba(0,0,0,.35) inset;
  flex:0 0 auto;
}
#ecAdminPanel .ecap-dot.ok{ background: rgba(120,255,170,.85); }
#ecAdminPanel .ecap-dot.bad{ background: rgba(255,84,84,.85); }
#ecAdminPanel .ecap-title{
  font-weight:750;
  letter-spacing:.2px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
#ecAdminPanel .ecap-subtitle{
  font-size:12px;
  color:var(--ecap-muted);
  font-weight:650;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  margin-top:1px;
}
#ecAdminPanel .ecap-titleBlock{ min-width:0; }
#ecAdminPanel .ecap-headBtns{ display:flex; gap:6px; align-items:center; flex:0 0 auto; }

#ecAdminPanel .ecap-btn,
#ecAdminPanel .ecap-iconBtn{
  background: rgba(232,240,255,.12);
  color: var(--ecap-text);
  border: 1px solid var(--ecap-border);
  border-radius: 12px;
  padding: 7px 10px;
  cursor:pointer;
  transition: transform .06s ease, background .12s ease, border-color .12s ease;
}
#ecAdminPanel .ecap-iconBtn{
  padding: 7px 9px;
  min-width: 36px;
  text-align:center;
}
#ecAdminPanel .ecap-btn:hover,
#ecAdminPanel .ecap-iconBtn:hover{ background: rgba(232,240,255,.18); border-color: var(--ecap-border2); }
#ecAdminPanel .ecap-btn:active,
#ecAdminPanel .ecap-iconBtn:active{ transform: translateY(1px); }

#ecAdminPanel .ecap-btn.primary{ border-color: rgba(124,179,255,.35); }
#ecAdminPanel .ecap-btn.danger,
#ecAdminPanel .ecap-iconBtn.danger{ border-color: rgba(255,84,84,.35); }

#ecAdminPanel .ecap-btn[disabled],
#ecAdminPanel .ecap-iconBtn[disabled]{ opacity:.55; cursor:not-allowed; transform:none; }
#ecAdminPanel .ecap-btn.isBusy,
#ecAdminPanel .ecap-iconBtn.isBusy,
#ecAdminPanel .ecap-btn[aria-busy="true"],
#ecAdminPanel .ecap-iconBtn[aria-busy="true"]{
  opacity:.72;
  cursor:progress;
  position:relative;
}
#ecAdminPanel .ecap-btn.isBusy::after,
#ecAdminPanel .ecap-iconBtn.isBusy::after{
  content:'…';
  display:inline-block;
  margin-left:4px;
  animation: ecapBusyPulse 900ms ease-in-out infinite;
}
@keyframes ecapBusyPulse{ 0%,100%{ opacity:.35; transform:translateY(0); } 50%{ opacity:1; transform:translateY(-1px); } }

#ecAdminPanel .ecap-radioStationRow{ align-items:flex-start; gap:10px; }
#ecAdminPanel .ecap-radioStationFields{ min-width:0; flex:1 1 auto; }
#ecAdminPanel .ecap-radioStationActions{ display:flex; flex-direction:column; gap:6px; flex:0 0 auto; }
@media (max-width: 760px){
  #ecAdminPanel .ecap-radioStationRow{ flex-direction:column; align-items:stretch; }
  #ecAdminPanel .ecap-radioStationActions{ flex-direction:row; flex-wrap:wrap; }
}


#ecAdminPanel .ecap-profilePostText{
  white-space:pre-wrap;
  overflow:hidden;
  display:-webkit-box;
  -webkit-line-clamp:3;
  -webkit-box-orient:vertical;
}
#ecAdminPanel .ecap-profilePostMeta{ font-size:12px; color:var(--ecap-muted); margin-top:4px; }
#ecAdminPanel .ecap-profileCommentBox{ margin-top:8px; padding-left:10px; border-left:1px solid rgba(226,238,255,.18); }

#ecAdminPanel input, #ecAdminPanel select, #ecAdminPanel textarea{
  width:100%;
  padding:9px 10px;
  border-radius:12px;
  background: rgba(8,12,18,.88);
  border: 1px solid rgba(226,238,255,.30);
  color: var(--ecap-text);
  outline:none;
}
#ecAdminPanel textarea{ min-height:64px; resize:vertical; }

#ecAdminPanel input:focus-visible,
#ecAdminPanel select:focus-visible,
#ecAdminPanel textarea:focus-visible,
#ecAdminPanel .ecap-btn:focus-visible,
#ecAdminPanel .ecap-iconBtn:focus-visible{
  box-shadow: 0 0 0 3px rgba(124,179,255,.22);
  border-color: rgba(124,179,255,.45);
}

#ecAdminPanel .ecap-body{
  padding: 10px 12px 12px;
  overflow:auto;
  max-height: calc(100vh - 72px);
}

/*
  Autosizing / layout improvements
  - In maximized mode, make the active section + its primary list stretch to fill the panel.
  - Prevents a large “blank area” under short lists (e.g., Audit tab) when the panel is tall.
*/
#ecAdminPanel.ecap-max{
  display:flex;
  flex-direction:column;
}
#ecAdminPanel.ecap-max .ecap-body{
  display:flex;
  flex-direction:column;
  flex: 1 1 auto;
  min-height: 0;
  overflow:hidden;
}
#ecAdminPanel.ecap-max .ecap-tabs{ flex: 0 0 auto; }
#ecAdminPanel.ecap-max .ecap-toastStack{ flex: 0 0 auto; }
#ecAdminPanel.ecap-max .ecap-section.active{
  display:flex;
  flex-direction:column;
  flex: 1 1 auto;
  min-height: 0;
  overflow:auto;
}

/* Utility classes used by sections that should stretch in max mode */
#ecAdminPanel.ecap-max .ecap-fill{ flex: 1 1 auto; min-height: 0; }
#ecAdminPanel.ecap-max .ecap-fillCol{ display:flex; flex-direction:column; }
#ecAdminPanel.ecap-max .ecap-fillScroll{
  flex: 1 1 auto;
  min-height: 0;
  overflow:auto;
  max-height:none !important;
}

#ecAdminPanel .ecap-tabs{
  display:block;
  margin: 0 0 10px;
}
#ecAdminPanel .ecap-navShell{
  border:1px solid rgba(226,238,255,.18);
  border-radius:16px;
  background: rgba(8,12,18,.34);
  padding:10px;
}
#ecAdminPanel .ecap-navHead{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
  margin-bottom:9px;
}
#ecAdminPanel .ecap-navTitle{
  font-size:12px;
  font-weight:850;
  letter-spacing:.32px;
  text-transform:uppercase;
  color:var(--ecap-text);
}
#ecAdminPanel .ecap-navHint{
  color:var(--ecap-faint);
  font-size:11px;
  margin-top:2px;
}
#ecAdminPanel .ecap-tabGroups{
  display:grid;
  grid-template-columns:repeat(5, minmax(0,1fr));
  gap:8px;
}
#ecAdminPanel .ecap-tabGroup{
  border:1px solid rgba(226,238,255,.14);
  border-radius:14px;
  background: rgba(255,255,255,.035);
  padding:8px;
  min-width:0;
}
#ecAdminPanel .ecap-tabGroupTitle{
  color:var(--ecap-muted);
  font-size:11px;
  font-weight:800;
  letter-spacing:.2px;
  margin:0 0 6px;
}
#ecAdminPanel .ecap-tabGroupBtns{
  display:flex;
  flex-direction:column;
  gap:6px;
}
#ecAdminPanel .ecap-tab{
  width:100%;
  border-radius:12px;
  padding: 8px 9px;
  border:1px solid rgba(226,238,255,.26);
  background: rgba(232,240,255,.10);
  color: var(--ecap-text);
  font-weight: 650;
  cursor:pointer;
  display:flex;
  gap:8px;
  align-items:center;
  justify-content:flex-start;
  text-align:left;
}
#ecAdminPanel .ecap-tab .ico{ opacity:.95; flex:0 0 auto; }
#ecAdminPanel .ecap-tab:focus-visible{ outline:3px solid rgba(124,179,255,.26); outline-offset:2px; }
#ecAdminPanel .ecap-tab.active{
  background: rgba(142,195,255,.22);
  border-color: rgba(142,195,255,.55);
  box-shadow: inset 0 0 0 1px rgba(142,195,255,.18);
}
#ecAdminPanel .ecap-sectionHero{
  border:1px solid rgba(142,195,255,.22);
  background: linear-gradient(135deg, rgba(142,195,255,.14), rgba(255,255,255,.035));
  border-radius:16px;
  padding:12px;
  margin: 0 0 10px;
}
#ecAdminPanel .ecap-sectionHero .eyebrow{
  color:var(--ecap-accent);
  font-size:11px;
  font-weight:850;
  letter-spacing:.34px;
  text-transform:uppercase;
  margin-bottom:3px;
}
#ecAdminPanel .ecap-sectionHero h4{ margin:0 0 4px; font-size:14px; }
#ecAdminPanel .ecap-gotoGrid{ display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:8px; margin-top:10px; }
#ecAdminPanel .ecap-gotoBtn{ justify-content:flex-start; text-align:left; min-height:42px; }
#ecAdminPanel .ecap-gotoBtn b{ display:block; font-size:12px; }
#ecAdminPanel .ecap-gotoBtn span{ display:block; color:var(--ecap-faint); font-size:11px; margin-top:1px; }

#ecAdminPanel .ecap-section{ display:none; }
#ecAdminPanel .ecap-section.active{ display:block; }

#ecAdminPanel .ecap-grid2{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
#ecAdminPanel .ecap-soundTestRow{ display:flex; gap:8px; align-items:center; }
#ecAdminPanel .ecap-soundTestRow select{ min-width:0; flex:1 1 auto; }
#ecAdminPanel .ecap-soundTestBtn{ flex:0 0 auto; white-space:nowrap; padding-left:12px; padding-right:12px; }
#ecAdminPanel .ecap-grid3{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }
#ecAdminPanel .ecap-settingsGroups{ display:flex; flex-direction:column; gap:10px; }
#ecAdminPanel .ecap-settingsGroup{
  border:1px solid rgba(226,238,255,.18);
  background: rgba(8,12,18,.28);
  border-radius:14px;
  padding:10px;
}
#ecAdminPanel .ecap-settingsGroupHead{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
  margin:0 0 10px;
}
#ecAdminPanel .ecap-settingsGroupTitle{
  font-weight:820;
  color:var(--ecap-text);
  letter-spacing:.18px;
}
#ecAdminPanel .ecap-settingsGroupDesc{
  color:var(--ecap-faint);
  font-size:12px;
  margin-top:2px;
}
#ecAdminPanel .ecap-settingsGroup.badge-risk{
  border-color: rgba(255,199,115,.32);
  background: linear-gradient(135deg, rgba(255,199,115,.10), rgba(8,12,18,.28));
}
#ecAdminPanel .ecap-settingsSummary{
  display:grid;
  grid-template-columns:repeat(4, minmax(0,1fr));
  gap:8px;
  margin:0 0 10px;
}
#ecAdminPanel .ecap-settingsSummary .ecap-pill{
  justify-content:center;
  min-height:28px;
  text-align:center;
}
#ecAdminPanel .ecap-settingsGroup .ecap-grid2{ margin-top:0; }
#ecAdminPanel .ecap-row{ display:flex; gap:8px; align-items:center; margin:8px 0; }
#ecAdminPanel .ecap-row > *{ flex:1; min-width:0; }
#ecAdminPanel .tight{ flex:0 0 auto; }

#ecAdminPanel .ecap-userSearchBar{
  display:grid;
  grid-template-columns:minmax(0,1.8fr) 132px 92px;
  gap:8px;
  align-items:center;
  margin:8px 0;
}
#ecAdminPanel .ecap-userSearchBar > *{ min-width:0; }
#ecAdminPanel .ecap-userFilterRow{
  display:flex;
  gap:8px;
  align-items:center;
  margin:8px 0;
  flex-wrap:wrap;
}
#ecAdminPanel .ecap-userFilterRow .ecap-pill{ flex:0 0 auto; }
#ecAdminPanel .ecap-userFilterRow select{ flex:0 0 148px; }
#ecAdminPanel .ecap-userFilterHint{ flex:1 1 180px; min-width:160px; }
#ecAdminPanel .ecap-userPager{
  display:flex;
  gap:8px;
  align-items:center;
  justify-content:space-between;
  margin:8px 0;
}
#ecAdminPanel .ecap-userPager .ecap-muted{ flex:1 1 auto; text-align:center; }
#ecAdminPanel .ecap-userPager button{ flex:0 0 auto; }
#ecAdminPanel .ecap-userPager button:disabled{ opacity:.45; cursor:not-allowed; }
#ecAdminPanel .ecap-hr{ height:1px; background:rgba(226,238,255,.18); margin:12px 0; }

#ecAdminPanel .ecap-card{
  border:1px solid rgba(226,238,255,.24);
  background: var(--ecap-surface);
  border-radius:14px;
  padding: 12px;
  margin: 10px 0;
}
#ecAdminPanel .ecap-card h4{
  margin:0 0 8px 0;
  font-size: 13px;
  letter-spacing:.24px;
  opacity:1;
  color: var(--ecap-text);
}
#ecAdminPanel .ecap-muted{ color: var(--ecap-muted); font-size: 12px; }
#ecAdminPanel .ecap-kv{ display:grid; grid-template-columns: 1fr auto; gap: 7px 10px; }
#ecAdminPanel .ecap-k{ color: var(--ecap-muted); font-weight:600; }
#ecAdminPanel .ecap-v{ font-weight: 800; color: var(--ecap-text); }

#ecAdminPanel .ecap-statGrid{ display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; }
#ecAdminPanel .ecap-stat{
  border:1px solid rgba(226,238,255,.22);
  background: var(--ecap-surface2);
  border-radius:14px;
  padding:12px;
  min-height:54px;
}
#ecAdminPanel .ecap-stat .lbl{ color: var(--ecap-muted); font-size: 12px; font-weight:600; }
#ecAdminPanel .ecap-stat .val{ font-weight: 820; font-size: 17px; margin-top:4px; color:var(--ecap-text); }
#ecAdminPanel .ecap-trendGrid{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:10px; }
#ecAdminPanel .ecap-metric{
  border:1px solid rgba(255,255,255,.10);
  background: rgba(255,255,255,.04);
  border-radius:14px;
  padding:10px;
}
#ecAdminPanel .ecap-metric .lbl{ color: var(--ecap-muted); font-size: 12px; font-weight:600; }
#ecAdminPanel .ecap-metric .val{ font-weight:820; font-size:18px; margin-top:4px; color:var(--ecap-text); }
#ecAdminPanel .ecap-metric .meta{ color: var(--ecap-faint); font-size: 11px; margin-top:4px; }
#ecAdminPanel .ecap-chartCard{
  border:1px solid rgba(255,255,255,.10);
  background: rgba(255,255,255,.04);
  border-radius:14px;
  padding:10px;
}
#ecAdminPanel .ecap-chartHead{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom:8px; }
#ecAdminPanel .ecap-chartHead .title{ font-weight:800; font-size:13px; color:var(--ecap-text); }
#ecAdminPanel .ecap-chartHead .sub{ color: var(--ecap-faint); font-size:11px; }
#ecAdminPanel .ecap-barChart{ display:flex; align-items:flex-end; gap:6px; min-height:120px; padding:8px 0 2px; }
#ecAdminPanel .ecap-barChartTall{ min-height:150px; }
#ecAdminPanel .ecap-barCol{ flex:1 1 0; min-width:0; display:flex; flex-direction:column; align-items:center; gap:6px; }
#ecAdminPanel .ecap-barWrap{ width:100%; height:108px; display:flex; align-items:flex-end; }
#ecAdminPanel .ecap-barChartTall .ecap-barWrap{ height:138px; }
#ecAdminPanel .ecap-bar{ width:100%; border-radius:10px 10px 4px 4px; background: linear-gradient(180deg, rgba(124,179,255,.95), rgba(124,179,255,.38)); border:1px solid rgba(124,179,255,.25); min-height:4px; }
#ecAdminPanel .ecap-bar.warn{ background: linear-gradient(180deg, rgba(255,199,107,.95), rgba(255,199,107,.35)); border-color: rgba(255,199,107,.28); }
#ecAdminPanel .ecap-bar.bad{ background: linear-gradient(180deg, rgba(255,84,84,.95), rgba(255,84,84,.35)); border-color: rgba(255,84,84,.28); }
#ecAdminPanel .ecap-barLbl{ color: var(--ecap-faint); font-size: 11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
#ecAdminPanel .ecap-barVal{ font-size:11px; color: var(--ecap-text); font-weight:700; }
#ecAdminPanel .ecap-list.compact .ecap-item{ padding:8px 10px; }

#ecAdminPanel .ecap-list{
  max-height: 210px;
  overflow:auto;
  border-radius: 14px;
  border:1px solid rgba(226,238,255,.22);
  background: rgba(8,12,18,.72);
}
#ecAdminPanel .ecap-item{
  display:flex; align-items:center; justify-content:space-between; gap:10px;
  padding: 9px 10px;
  border-bottom:1px solid rgba(226,238,255,.14);
  min-width:0;
}
#ecAdminPanel .ecap-item > *{ min-width:0; }
#ecAdminPanel .ecap-item .ecap-actions,
#ecAdminPanel .ecap-item > div:last-child{ flex-wrap:wrap; max-width:100%; }
#ecAdminPanel .ecap-item a,
#ecAdminPanel .ecap-item code,
#ecAdminPanel .ecap-item pre,
#ecAdminPanel .ecap-item .ecap-muted{ overflow-wrap:anywhere; word-break:break-word; }
#ecAdminPanel .ecap-item:last-child{ border-bottom:none; }
#ecAdminPanel .ecap-item:hover{ background: rgba(232,240,255,.10); }

#ecAdminPanel .ecap-pill{
  display:inline-flex; align-items:center; gap:6px;
  padding: 2px 9px;
  border-radius:999px;
  border:1px solid rgba(226,238,255,.26);
  background: rgba(8,12,18,.42);
  font-size: 12px;
  color: var(--ecap-text);
  font-weight:600;
  max-width:100%;
  overflow:hidden;
  text-overflow:ellipsis;
}
#ecAdminPanel .ecap-pill.ok{ border-color: rgba(120,255,170,.35); }
#ecAdminPanel .ecap-pill.warn{ border-color: rgba(255,199,107,.35); }
#ecAdminPanel .ecap-pill.bad{ border-color: rgba(255,84,84,.35); }

#ecAdminPanel .ecap-actions{ display:flex; flex-wrap:wrap; gap:6px; }

#ecAdminPanel .ecap-drop{
  border:1px dashed rgba(226,238,255,.34);
  border-radius:14px;
  padding:10px;
  background: rgba(232,240,255,.06);
}
#ecAdminPanel .ecap-drop.dragover{
  background: rgba(124,179,255,.10);
  border-color: rgba(124,179,255,.35);
}

#ecAdminPanel .ecap-log{
  max-height: 150px;
  overflow:auto;
  padding: 10px;
  border-radius: 14px;
  background: rgba(3,7,12,.90);
  border: 1px solid rgba(226,238,255,.22);
  font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
  font-size: 12px;
  color: var(--ecap-text);
  white-space: pre-wrap;
}

#ecAdminPanel .ecap-toastStack{
  position: sticky;
  top: 0;
  display:flex;
  flex-direction:column;
  gap:6px;
  margin: 0 0 10px;
  z-index: 1;
}
#ecAdminPanel .ecap-toast{
  border-radius: 14px;
  border:1px solid rgba(226,238,255,.24);
  background: rgba(18,25,35,.96);
  padding: 8px 10px;
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:10px;
}
#ecAdminPanel .ecap-toast .tmsg{ color: var(--ecap-text); font-weight:600; }
#ecAdminPanel .ecap-toast .tmeta{ color: var(--ecap-faint); font-size: 12px; margin-top:2px; }
#ecAdminPanel .ecap-toast.ok{ border-color: rgba(120,255,170,.25); }
#ecAdminPanel .ecap-toast.warn{ border-color: rgba(255,199,107,.25); }
#ecAdminPanel .ecap-toast.err{ border-color: rgba(255,84,84,.25); }
#ecAdminPanel .ecap-toast .x{
  opacity:.8;
  cursor:pointer;
  padding:0 6px;
  background:transparent;
  border:0;
  color:var(--ecap-text);
  border-radius:8px;
  min-width:28px;
  min-height:28px;
  line-height:1;
}
#ecAdminPanel .ecap-toast .x:hover,
#ecAdminPanel .ecap-toast .x:focus-visible{ opacity:1; background:rgba(232,240,255,.12); }
#ecAdminPanel .ecap-toast[role="status"]{ outline:0; }


#ecAdminPanel .ecap-modalBackdrop{
  position:absolute;
  inset:0;
  display:none;
  align-items:center;
  justify-content:center;
  padding:16px;
  background: rgba(6,8,12,.72);
  z-index:5;
}
#ecAdminPanel .ecap-modalBackdrop.open,
#ecAdminPanel .ecap-modalBackdrop[aria-hidden="false"]{ display:flex; }

#ecAdminPanel .ecap-modal{
  width:min(420px, calc(100% - 8px));
  max-height:calc(100vh - 72px);
  overflow:auto;
  border-radius:16px;
  border:1px solid rgba(226,238,255,.30);
  background: linear-gradient(180deg, rgba(31,39,52,.99), rgba(16,22,31,.99));
  box-shadow: 0 24px 60px rgba(0,0,0,.48);
  padding:14px;
}
#ecAdminPanel .ecap-modalTitle{
  font-size:15px;
  font-weight:800;
  letter-spacing:.2px;
  margin:0 0 6px 0;
}
#ecAdminPanel .ecap-modalText{
  color:var(--ecap-muted);
  font-size:13px;
  line-height:1.45;
  margin:0 0 12px 0;
}
#ecAdminPanel .ecap-modalActions{
  display:flex;
  justify-content:flex-end;
  gap:8px;
  margin-top:12px;
}
#ecAdminPanel .ecap-fieldLabel{
  display:block;
  margin:0 0 6px 0;
  font-size:12px;
  color:var(--ecap-muted);
}
#ecAdminPanel .ecap-errorText{
  min-height:18px;
  margin-top:8px;
  color: rgba(255,156,156,.96);
  font-size:11px;
}
#ecAdminPanel .ecap-busyNote{
  min-height:18px;
  margin-top:6px;
  color: var(--ecap-muted);
  font-size:11px;
}
#ecAdminPanel .ecap-passMeter{
  margin-top:8px;
  padding:10px;
  border:1px solid rgba(226,238,255,.20);
  border-radius:12px;
  background: rgba(8,12,18,.42);
}
#ecAdminPanel .ecap-passMeterTop{
  display:flex;
  justify-content:space-between;
  gap:10px;
  color:var(--ecap-muted);
  font-size:12px;
  font-weight:750;
}
#ecAdminPanel .ecap-passMeterBar{
  height:7px;
  overflow:hidden;
  border-radius:999px;
  background:rgba(226,238,255,.13);
  margin-top:8px;
}
#ecAdminPanel .ecap-passMeterFill{
  display:block;
  width:0;
  height:100%;
  border-radius:inherit;
  background:rgba(142,195,255,.90);
  transition:width .18s ease;
}
#ecAdminPanel .ecap-passRules{
  list-style:none;
  margin:8px 0 0;
  padding:0;
  display:grid;
  gap:5px;
  color:var(--ecap-muted);
  font-size:12px;
}
#ecAdminPanel .ecap-passRules li.pass{ color:var(--ecap-ok); }

#ecAdminPanel .ecap-usernameAvailability{
  margin:6px 0 2px 0;
  min-height:18px;
  color:var(--ecap-muted);
  font-size:12px;
  line-height:1.35;
}
#ecAdminPanel .ecap-usernameAvailability::before{
  content:'•';
  display:inline-block;
  width:1.15em;
  font-weight:900;
  color:currentColor;
}
#ecAdminPanel .ecap-usernameAvailability.checking{ color:#a9cfff; }
#ecAdminPanel .ecap-usernameAvailability.available{ color:var(--ecap-ok); }
#ecAdminPanel .ecap-usernameAvailability.taken,
#ecAdminPanel .ecap-usernameAvailability.invalid{ color:rgba(255,156,156,.96); }
#ecAdminPanel .ecap-usernameAvailability.unknown{ color:var(--ecap-warn); }
#ecAdminPanel .ecap-usernameAvailability.available::before{ content:'✓'; }
#ecAdminPanel .ecap-usernameAvailability.taken::before,
#ecAdminPanel .ecap-usernameAvailability.invalid::before{ content:'!'; }

#ecAdminPanel.ecap-mini .ecap-body{ display:none; }
#ecAdminPanel.ecap-mini{ height:52px !important; max-height:52px !important; min-height:52px !important; resize:none; }

#ecAdminPanel.ecap-max{
  width: 860px !important;
  height: calc(100vh - 32px) !important;
  resize:none;
}
#ecAdminPanel.ecap-max .ecap-body{ max-height: none; height: calc(100vh - 72px); }

@media (max-width: 860px){
  #ecAdminPanel .ecap-tabGroups{ grid-template-columns:repeat(2, minmax(0,1fr)); }
  #ecAdminPanel .ecap-gotoGrid{ grid-template-columns:repeat(2, minmax(0,1fr)); }
}

@media (max-width: 680px){
  #ecAdminPanel .ecap-row{ flex-wrap:wrap; align-items:stretch; }
  #ecAdminPanel .ecap-row > *{ flex:1 1 180px; }
  #ecAdminPanel .ecap-actions{ align-items:stretch; }
  #ecAdminPanel .ecap-actions .ecap-btn{ flex:1 1 auto; }
  #ecAdminPanel .ecap-userSearchBar{ grid-template-columns:1fr; }
  #ecAdminPanel .ecap-settingsSummary{ grid-template-columns:1fr; }
  #ecAdminPanel .ecap-settingsGroup .ecap-grid2{ grid-template-columns:1fr; }
  #ecAdminPanel .ecap-userFilterRow select{ flex:1 1 180px; }
  #ecAdminPanel .ecap-tabGroups{ grid-template-columns:1fr; }
  #ecAdminPanel .ecap-gotoGrid{ grid-template-columns:1fr; }
}

@media (max-width: 520px){
  #ecAdminPanel{ right:10px; left:10px; width:auto; min-width:0; max-width:none; resize:none; }
}

"""

    js = r"""

(function(){
  if (!window || !document) return;
  if (!window.IS_ADMIN) return;

  const SERVER_NAME = String((window.ECHOCHAT_CFG && window.ECHOCHAT_CFG.server_name) || 'Echo-Chat').trim() || 'Echo-Chat';
  const SERVER_ADMIN_NAME = `${SERVER_NAME} Admin`;
  const ECAP_USERNAME_MIN = __ECHOCHAT_USERNAME_MIN__;
  const ECAP_USERNAME_MAX = __ECHOCHAT_USERNAME_MAX__;
  const ECAP_USERNAME_PATTERN = __ECHOCHAT_USERNAME_PATTERN__;
  const ECAP_USERNAME_TITLE = __ECHOCHAT_USERNAME_TITLE__;
  const ECAP_PASSWORD_MIN = __ECHOCHAT_PASSWORD_MIN__;
  const ECAP_PASSWORD_MAX = __ECHOCHAT_PASSWORD_MAX__;
  const ECAP_PASSWORD_RECOMMENDED = __ECHOCHAT_PASSWORD_RECOMMENDED__;
  const ECAP_PASSWORD_SUMMARY = __ECHOCHAT_PASSWORD_SUMMARY__;
  const ECAP_PASSWORD_COMMON_WEAK = new Set(__ECHOCHAT_PASSWORD_COMMON_WEAK__);

  function ecapStoragePart(v, fallback){
    return String(v || fallback || 'default').trim().toLowerCase().replace(/[^a-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64) || String(fallback || 'default');
  }
  const ECAP_STATE_SCOPE = `${ecapStoragePart(SERVER_NAME, 'echo-chat')}:${ecapStoragePart(window.USERNAME || window.CURRENT_USER || 'admin', 'admin')}`;
  const STATE_KEY = `ecap_state_v4:${ECAP_STATE_SCOPE}`;
  const LEGACY_STATE_KEYS = ['ecap_state_v3'];
  const state = (()=>{
    try{
      const current = JSON.parse(localStorage.getItem(STATE_KEY)||'{}') || {};
      if (current && Object.keys(current).length) return current;
      for (const legacyKey of LEGACY_STATE_KEYS){
        const legacy = JSON.parse(localStorage.getItem(legacyKey)||'{}') || {};
        if (legacy && Object.keys(legacy).length){
          localStorage.setItem(STATE_KEY, JSON.stringify(legacy));
          return legacy;
        }
      }
    }catch(_){ }
    return {};
  })();
  function saveState(){ try{ localStorage.setItem(STATE_KEY, JSON.stringify(state)); }catch(_){ } }

  function clampPanelRect(panel){
    if (!panel) return;
    try{
      const pad = 8;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const r = panel.getBoundingClientRect();
      if (!vw || !vh || !r.width || !r.height) return;
      const maxLeft = Math.max(pad, vw - Math.min(r.width, vw - (pad * 2)) - pad);
      const maxTop = Math.max(pad, vh - Math.min(r.height, vh - (pad * 2)) - pad);
      const nextLeft = Math.max(pad, Math.min(maxLeft, r.left));
      const nextTop = Math.max(pad, Math.min(maxTop, r.top));
      if (Math.abs(nextLeft - r.left) > 1 || Math.abs(nextTop - r.top) > 1){
        panel.style.left = `${Math.round(nextLeft)}px`;
        panel.style.top = `${Math.round(nextTop)}px`;
        panel.style.right = 'auto';
        state.left = Math.round(nextLeft);
        state.top = Math.round(nextTop);
        saveState();
      }
    }catch(_){ }
  }

  function restorePanelSize(panel){
    if (!panel || state.max || state.mini) return;
    try{
      const w = parseInt(state.width || 0, 10);
      const h = parseInt(state.height || 0, 10);
      if (w >= 360) panel.style.width = `${Math.min(w, Math.max(360, (window.innerWidth || w) - 32))}px`;
      if (h >= 360) panel.style.height = `${Math.min(h, Math.max(360, (window.innerHeight || h) - 32))}px`;
    }catch(_){ }
  }

  // Panel reference + recovery helpers (prevents “blank panel” and allows hotkey reopen)
  let panelRef = null;
  function getPanel(){ return panelRef || document.getElementById('ecAdminPanel'); }

  function ensurePanel(){
    let p = document.getElementById('ecAdminPanel');
    if (!p){
      buildPanel();
      p = document.getElementById('ecAdminPanel');
    }
    // If panel exists but looks incomplete, rebuild it.
    if (p && (!p.querySelector('.ecap-head') || !p.querySelector('.ecap-tabs'))){
      try{ p.remove(); }catch(_){}
      buildPanel();
      p = document.getElementById('ecAdminPanel');
    }
    panelRef = p;
    return p;
  }

  function showPanel(opts){
    const p = ensurePanel();
    if (!p) return null;
    p.classList.remove('ecap-hidden');
    state.closed = false;

    // If it was minimized, it can look “blank” — un-minimize when showing via hotkey.
    if (opts && opts.unmini){
      p.classList.remove('ecap-mini');
      p.style.height = '';
      p.style.maxHeight = '';
      state.mini = false;
    }

    // If it ended up off-screen, reset to default position.
    try{
      const r = p.getBoundingClientRect();
      const pad = 12;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const offscreen = (r.right < pad) || (r.left > vw - pad) || (r.bottom < pad) || (r.top > vh - pad);
      if (offscreen){
        p.style.left = '';
        p.style.top = '';
        p.style.right = '16px';
        state.left = undefined;
        state.top = undefined;
      }
    }catch(_){}

    clampPanelRect(p);
    saveState();
    return p;
  }

  function hidePanel(){
    const p = getPanel();
    if (!p) return;
    p.classList.add('ecap-hidden');
    state.closed = true;
    saveState();
  }

  function setAdminStartupLocked(locked){
    const p = getPanel();
    if (!p) return;
    p.classList.toggle('ecap-startup-locked', !!locked);
    try{
      p.setAttribute('aria-busy', locked ? 'true' : 'false');
      const body = p.querySelector('.ecap-body');
      if (body) body.setAttribute('aria-hidden', locked ? 'true' : 'false');
    }catch(_){ }
  }

  function openAdminPanel(){
    const p = showPanel({unmini:true});
    if (!adminStartupUnlocked) requestAdminPanelStartupUnlock();
    return p;
  }

  function togglePanel(){
    const p = ensurePanel();
    if (!p) return;
    const nowHidden = p.classList.toggle('ecap-hidden');
    state.closed = nowHidden;
    saveState();
    if (!nowHidden){
      // Ensure it doesn't look blank when reopening
      showPanel({unmini:true});
      if (!adminStartupUnlocked) requestAdminPanelStartupUnlock();
    }
  }

  function resetPanel(){
    try{ localStorage.removeItem(STATE_KEY); }catch(_){}
    try{ const p = document.getElementById('ecAdminPanel'); if (p) p.remove(); }catch(_){}
    panelRef = null;
    adminRuntimeStarted = false;
    adminStartupUnlocked = false;
    try{ buildPanel(); }catch(e){ console.error(e); }
    showPanel({unmini:true});
    requestAdminPanelStartupUnlock();
  }

  async function openAdminTestLab(){
    let win = null;
    try{
      // Open a blank tab immediately from the click event, then move it to
      // the server-minted random URL after the admin-only request succeeds.
      try{ win = window.open('about:blank', '_blank'); }catch(_){ win = null; }
      if (win) { try{ win.opener = null; }catch(_){} }
      const r = await adminFetch('/admin/test_lab/link', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({})
      });
      const j = await r.json().catch(()=>null);
      if (!r.ok || !(j && j.ok && j.url)){
        const msg = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
        if (win) { try{ win.close(); }catch(_){} }
        throw new Error(msg);
      }
      if (win) win.location.href = j.url;
      else {
        win = window.open(j.url, '_blank', 'noopener,noreferrer');
        if (win) win.opener = null;
      }
      log('opened admin test lab with randomized admin-session link');
      toast('ok', 'Test Lab opened', 'A fresh randomized admin-only link was generated for this session.');
    }catch(err){
      const msg = err && err.message ? err.message : 'popup blocked or link request failed';
      log(`ERROR opening admin test lab :: ${msg}`);
      toast('err', 'Could not open Test Lab', msg);
    }
  }

  // Hotkeys:
  // - Ctrl+Alt+P toggles the admin panel (re-open even after “close”)
  // - Ctrl+Alt+Shift+P resets panel state + rebuilds (fixes “blank/bugged” panels)
  document.addEventListener('keydown', (e)=>{
    try{
      const k = (e && e.key ? String(e.key) : '').toLowerCase();
      if (e && e.ctrlKey && e.altKey && e.shiftKey && k === 'p'){
        e.preventDefault();
        resetPanel();
        return;
      }
      if (e && e.ctrlKey && e.altKey && k === 'p'){
        e.preventDefault();
        togglePanel();
        return;
      }
    }catch(_){}
  }, true);

  window.ECAP = window.ECAP || {};
  window.ECAP.show = openAdminPanel;
  window.ECAP.hide = hidePanel;
  window.ECAP.toggle = togglePanel;
  window.ECAP.reset = resetPanel;

  const logLines = [];
  function log(msg){
    const s = `[admin] ${new Date().toISOString()} ${msg}`;
    logLines.push(s);
    if (logLines.length > 200) logLines.shift();
    const el = document.querySelector('#ecAdminPanel .ecap-log');
    if (el) el.textContent = logLines.join('\n');
  }

  function getCookie(name){
    const parts = (`; ${document.cookie}`).split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
  }

  function fmtUptime(sec){
    sec = Math.max(0, parseInt(sec||0, 10)||0);
    const d = Math.floor(sec/86400); sec -= d*86400;
    const h = Math.floor(sec/3600); sec -= h*3600;
    const m = Math.floor(sec/60); sec -= m*60;
    const parts = [];
    if (d) parts.push(`${d}d`);
    if (h || d) parts.push(`${h}h`);
    if (m || h || d) parts.push(`${m}m`);
    parts.push(`${sec}s`);
    return parts.join(' ');
  }

  function debounce(fn, ms){
    let t = null;
    return (...args)=>{
      if (t) clearTimeout(t);
      t = setTimeout(()=>fn(...args), ms);
    };
  }

  const ecapPendingActions = new Set();

  function normalizeHeadersForAdminFetch(options){
    options = options || {};
    const src = options.headers;
    const out = {};
    try{
      if (src instanceof Headers){
        src.forEach((value, key)=>{ out[key] = value; });
      } else if (Array.isArray(src)){
        src.forEach((pair)=>{ if (pair && pair.length >= 2) out[String(pair[0])] = String(pair[1]); });
      } else if (src && typeof src === 'object'){
        Object.assign(out, src);
      }
    }catch(_){ }
    options.headers = out;
    return out;
  }

  function hasHeader(headers, name){
    const want = String(name || '').toLowerCase();
    return Object.keys(headers || {}).some(k => String(k).toLowerCase() === want);
  }

  function removeHeader(headers, name){
    const want = String(name || '').toLowerCase();
    for (const k of Object.keys(headers || {})){
      if (String(k).toLowerCase() === want) delete headers[k];
    }
  }

  function attachFreshAccessCsrf(options, force){
    const headers = normalizeHeadersForAdminFetch(options || {});
    if (force) removeHeader(headers, 'X-CSRF-TOKEN');
    if (!force && hasHeader(headers, 'X-CSRF-TOKEN')) return headers;
    const csrf = getCookie('csrf_access_token');
    if (csrf) headers['X-CSRF-TOKEN'] = csrf;
    return headers;
  }

  function setButtonBusy(btn, busy, label){
    if (!btn) return;
    if (busy){
      if (btn.dataset.ecapOriginalText === undefined) btn.dataset.ecapOriginalText = btn.textContent || '';
      if (btn.dataset.ecapOriginalHtml === undefined) btn.dataset.ecapOriginalHtml = btn.innerHTML || '';
      btn.classList.add('isBusy');
      btn.setAttribute('aria-busy', 'true');
      btn.disabled = true;
      if (label) btn.textContent = label;
      return;
    }
    btn.classList.remove('isBusy');
    btn.removeAttribute('aria-busy');
    btn.disabled = false;
    if (btn.dataset.ecapOriginalHtml !== undefined){
      btn.innerHTML = btn.dataset.ecapOriginalHtml;
      delete btn.dataset.ecapOriginalHtml;
      delete btn.dataset.ecapOriginalText;
    } else if (btn.dataset.ecapOriginalText !== undefined){
      btn.textContent = btn.dataset.ecapOriginalText;
      delete btn.dataset.ecapOriginalText;
    }
  }

  async function withAdminAction(btn, key, busyLabel, fn){
    const actionKey = String(key || (btn && btn.id) || 'admin-action');
    if (!adminStartupUnlocked){
      const unlocked = await requestAdminPanelStartupUnlock();
      if (!unlocked) return null;
    }
    if (ecapPendingActions.has(actionKey)){
      toast('warn', 'Action already running', btn && btn.textContent ? btn.textContent : 'Please wait for the current admin action to finish.');
      return null;
    }
    ecapPendingActions.add(actionKey);
    setButtonBusy(btn, true, busyLabel || 'Working');
    try{
      return await fn();
    } finally {
      ecapPendingActions.delete(actionKey);
      setButtonBusy(btn, false);
    }
  }

  async function refreshAccessToken(){
    try{
      const csrf = getCookie('csrf_refresh_token');
      const headers = csrf ? {'X-CSRF-TOKEN': csrf} : {};
      const r = await fetch('/token/refresh', {method:'POST', credentials:'include', headers});
      if (!r.ok) return false;
      const j = await r.json().catch(()=>({}));
      return !!(j && (j.ok === true || j.status === 'ok' || j.success === true));
    }catch(_){
      return false;
    }
  }

  let adminReauthPromise = null;
  let adminReauthStatusPromise = null;
  let adminReauthSessionCache = {key:null, confirmedAt:0, oncePerSession:false};

  // Startup gate: the injected admin shell may exist, but live admin data and
  // actions must not start until the admin confirms the current password.
  let adminStartupUnlocked = false;
  let adminStartupUnlockPromise = null;
  let adminRuntimeStarted = false;
  let adminIntervalsStarted = false;

  // Functions such as refreshVoiceSettings() live inside buildPanel() because
  // they close over DOM nodes created there.  Startup unlock/runtime code lives
  // outside buildPanel() so hotkey/open/reset flows can reuse it.  Keep a
  // narrow bridge here so the runtime never calls inner-scope functions by
  // bare name after the password gate unlocks.
  const adminRuntimeFns = {
    refreshVoiceSettings: async ()=>{},
    refreshIceSettings: async ()=>{},
    refreshMediaStatus: async ()=>{},
    refreshStats: async ()=>{},
    refreshSecurityStatus: async ()=>{},
    refreshDiagnostics: async ()=>{},
    refreshAnalytics: async ()=>{},
    runSearch: async ()=>{}
  };

  function _adminReauthSessionKey(meta){
    try{
      const sid = meta && meta.sid ? String(meta.sid).trim() : '';
      if (sid) return 'sid:' + sid;
      const actor = meta && meta.actor ? String(meta.actor).trim().toLowerCase() : '';
      if (actor) return 'actor:' + actor;
    }catch(_){ }
    return '';
  }

  function _markAdminReauthConfirmed(meta){
    try{
      const key = _adminReauthSessionKey(meta);
      if (!key) return;
      adminReauthSessionCache = {
        key,
        confirmedAt: Number(meta && meta.confirmed_at) || Math.floor(Date.now()/1000),
        oncePerSession: !!(meta && meta.once_per_session)
      };
    }catch(_){ }
  }

  function _clearAdminReauthConfirmed(){
    adminReauthSessionCache = {key:null, confirmedAt:0, oncePerSession:false};
  }

  function _adminReauthCacheMatches(meta){
    try{
      const key = _adminReauthSessionKey(meta);
      return !!(key && adminReauthSessionCache && adminReauthSessionCache.key === key && adminReauthSessionCache.confirmedAt);
    }catch(_){
      return false;
    }
  }

  async function ensureAdminReauthAlreadyFresh(meta){
    // A 428 can race with another admin request that is already showing or just
    // completed the password dialog.  Ask the server for the authoritative
    // current-session state before opening another prompt.
    if (adminReauthStatusPromise) return adminReauthStatusPromise;
    adminReauthStatusPromise = (async ()=>{
      try{
        let r = await fetch('/admin/auth/status', {method:'GET', credentials:'include'});
        if (r.status === 401){
          const refreshed = await refreshAccessToken();
          if (refreshed) r = await fetch('/admin/auth/status', {method:'GET', credentials:'include'});
        }
        if (r.status === 401 || r.status === 403){
          _clearAdminReauthConfirmed();
          return false;
        }
        const j = await r.json().catch(()=>null);
        if (r.ok && j && (j.ok === true || j.status === 'ok' || j.success === true) && !j.reauth_required){
          _markAdminReauthConfirmed(j);
          return true;
        }
        if (j && j.reauth_required){
          const statusKey = _adminReauthSessionKey(j);
          const metaKey = _adminReauthSessionKey(meta);
          if (statusKey && metaKey && statusKey !== metaKey) _clearAdminReauthConfirmed();
        }
        return false;
      }catch(_){
        return false;
      }finally{
        adminReauthStatusPromise = null;
      }
    })();
    return adminReauthStatusPromise;
  }

  async function confirmAdminPassword(message){
    if (adminReauthPromise) return adminReauthPromise;
    adminReauthPromise = (async ()=>{
      const panel = showPanel({unmini:true});
      const modal = ensureAdminPasswordModal(panel);
      if (!modal) return false;

      const titleEl = modal.querySelector('.ecap-modalTitle');
      const textEl = modal.querySelector('.ecap-modalText');
      const inputEl = modal.querySelector('input[name="current_password"]');
      const errorEl = modal.querySelector('.ecap-errorText');
      const busyEl = modal.querySelector('.ecap-busyNote');
      const cancelBtn = modal.querySelector('[data-act="cancel"]');
      const confirmBtn = modal.querySelector('[data-act="confirm"]');
      const formEl = modal.querySelector('form');

      if (!titleEl || !textEl || !inputEl || !errorEl || !busyEl || !cancelBtn || !confirmBtn || !formEl){
        return false;
      }

      titleEl.textContent = 'Admin password confirmation';
      textEl.textContent = message || 'Confirm your password to continue this admin action.';
      inputEl.value = '';
      inputEl.disabled = false;
      errorEl.textContent = '';
      busyEl.textContent = '';
      confirmBtn.textContent = 'Confirm';
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');

      const priorFocus = document.activeElement;
      let resolved = false;

      const finish = (value)=>{
        if (resolved) return;
        resolved = true;
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        inputEl.value = '';
        inputEl.disabled = false;
        errorEl.textContent = '';
        busyEl.textContent = '';
        cancelBtn.disabled = false;
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Confirm';
        try{
          if (priorFocus && typeof priorFocus.focus === 'function') priorFocus.focus();
          else ensurePanel()?.querySelector('.ecap-headBtns button')?.focus();
        }catch(_){ }
        return value;
      };

      const setBusy = (busy, text)=>{
        cancelBtn.disabled = !!busy;
        confirmBtn.disabled = !!busy;
        inputEl.disabled = !!busy;
        busyEl.textContent = busy ? (text || 'Checking password…') : '';
        confirmBtn.textContent = busy ? 'Checking…' : 'Confirm';
      };

      return await new Promise((resolve)=>{
        const cancel = ()=>{
          cleanup();
          resolve(finish(false));
        };

        const onKey = (e)=>{
          if (e.key === 'Escape'){
            e.preventDefault();
            if (!confirmBtn.disabled) cancel();
          }
        };

        const submit = async (e)=>{
          e.preventDefault();
          const pw = String(inputEl.value || '');
          if (!pw){
            errorEl.textContent = 'Enter your current password.';
            inputEl.focus();
            return;
          }
          errorEl.textContent = '';
          setBusy(true, 'Checking password…');
          try{
            const fd = new FormData();
            fd.append('current_password', pw);
            const confirmOpts = {method:'POST', credentials:'include', headers:{}, body:fd};
            attachFreshAccessCsrf(confirmOpts, true);
            let r = await fetch('/admin/auth/confirm', confirmOpts);
            if (r.status === 401){
              const refreshed = await refreshAccessToken();
              if (refreshed){
                attachFreshAccessCsrf(confirmOpts, true);
                r = await fetch('/admin/auth/confirm', confirmOpts);
              }
            }
            const j = await r.json().catch(()=>null);
            if (!r.ok || !(j && (j.ok === true || j.status === 'ok' || j.success === true))){
              const errMsg = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
              errorEl.textContent = errMsg;
              log(`ERROR ${r.status} POST /admin/auth/confirm :: ${errMsg}`);
              setBusy(false, '');
              try{ inputEl.focus(); inputEl.select(); }catch(_){ }
              return;
            }
            const secs = Number(j && j.remaining_seconds);
            _markAdminReauthConfirmed(j);
            cleanup();
            const oncePerSession = !!(j && j.once_per_session);
            panelToast('ok','Confirmed', oncePerSession ? 'Admin actions unlocked for this login session' : (Number.isFinite(secs) ? `Admin actions unlocked for ${secs}s` : 'Admin actions unlocked'));
            log(oncePerSession ? 'admin password confirmed for current login session' : 'admin password confirmed for fresh-write window');
            resolve(finish(true));
          }catch(err){
            const errMsg = err && err.message ? err.message : 'network error';
            errorEl.textContent = errMsg;
            setBusy(false, '');
            panelToast('err','Password confirmation failed', errMsg, 5200);
          }
        };

        const cleanup = ()=>{
          formEl.removeEventListener('submit', submit);
          cancelBtn.removeEventListener('click', cancel);
          modal.removeEventListener('keydown', onKey);
        };

        formEl.addEventListener('submit', submit);
        cancelBtn.addEventListener('click', cancel);
        modal.addEventListener('keydown', onKey);

        setTimeout(()=>{
          try{ inputEl.focus(); inputEl.select(); }catch(_){ }
        }, 0);
      });
    })();

    try{
      return await adminReauthPromise;
    }finally{
      adminReauthPromise = null;
    }
  }

  function _altAdminUrl(u){
    try{
      if (typeof u !== 'string') return null;
      if (u.startsWith('/api/admin/')) return '/admin/' + u.slice('/api/admin/'.length);
      if (u.startsWith('/admin/')) return '/api/admin/' + u.slice('/admin/'.length);
      return null;
    }catch(_){
      return null;
    }
  }

  async function adminFetch(url, opts){
    let u = url;
    const options = Object.assign({credentials:'include'}, opts||{});
    const method = (options.method || 'GET').toUpperCase();

    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      attachFreshAccessCsrf(options, false);
    } else {
      normalizeHeadersForAdminFetch(options);
    }

    let r = await fetch(u, options);

    if (r.status === 404){
      const alt = _altAdminUrl(u);
      if (alt){
        const r2 = await fetch(alt, options);
        if (r2.status !== 404){
          log(`INFO endpoint fallback: ${u} -> ${alt} (${r2.status})`);
          u = alt;
          r = r2;
        }
      }
    }

    if (r.status === 401){
      const ok = await refreshAccessToken();
      if (ok){
        if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') attachFreshAccessCsrf(options, true);
        r = await fetch(u, options);
      } else {
        _clearAdminReauthConfirmed();
      }
    }

    if (r.status === 428 && !String(u).includes('/admin/auth/confirm')){
      const meta = await r.clone().json().catch(()=>null);
      const needsConfirm = !!(meta && (meta.reauth_required || meta.code === 'admin_reauth_required'));
      if (needsConfirm){
        const alreadyFresh = _adminReauthCacheMatches(meta) || await ensureAdminReauthAlreadyFresh(meta);
        if (alreadyFresh){
          if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') attachFreshAccessCsrf(options, true);
          r = await fetch(u, options);
        } else {
          const onceText = meta && meta.once_per_session ? 'Confirm your password once to unlock admin actions for this login session.' : null;
          const ok = await confirmAdminPassword(onceText || (meta && meta.error ? meta.error : 'Confirm your password to continue this admin action:'));
          if (ok){
            if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') attachFreshAccessCsrf(options, true);
            r = await fetch(u, options);
          }
        }
      }
    }
    return r;
  }

  function _normalizeOk(j, httpOk){
    try{
      if (!j || typeof j !== 'object') j = {};
      // If server doesn't return {ok:true}, treat HTTP 2xx as success.
      if (j.ok === undefined){
        if (j.status === 'ok' || j.success === true) j.ok = true;
        else j.ok = !!httpOk;
      }
      return j;
    }catch(_){
      return {ok: !!httpOk};
    }
  }

  async function getJSON(url){
    const r = await adminFetch(url, {method:'GET'});
    const j = await r.json().catch(()=>null);
    if (!r.ok){
      const e = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
      log(`ERROR ${r.status} GET ${url} :: ${e}`);
      return {ok:false, error:e, _status:r.status, _url:url};
    }
    return _normalizeOk(j, r.ok);
  }

  async function postForm(url, data){
    const fd = new FormData();
    for (const [k,v] of Object.entries(data||{})) fd.append(k, v);
    const r = await adminFetch(url, {method:'POST', body: fd});
    const j = await r.json().catch(()=>null);
    if (!r.ok){
      const e = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
      log(`ERROR ${r.status} POST ${url} :: ${e}`);
      return {ok:false, error:e, _status:r.status, _url:url};
    }
    return _normalizeOk(j, r.ok);
  }

  async function postJSON(url, obj){
    const r = await adminFetch(url, {
      method:'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(obj||{})
    });
    const j = await r.json().catch(()=>null);
    if (!r.ok){
      const e = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
      log(`ERROR ${r.status} POST ${url} :: ${e}`);
      return {ok:false, error:e, _status:r.status, _url:url};
    }
    return _normalizeOk(j, r.ok);
  }

  function el(tag, attrs){
    const n = document.createElement(tag);
    if (attrs){
      for (const [k,v] of Object.entries(attrs)){
        if (v === undefined || v === null) continue;
        if (k === 'class') n.className = v;
        else if (k === 'text') n.textContent = String(v);
        else if (k === 'style') n.setAttribute('style', String(v));
        else n.setAttribute(k, String(v));
      }
    }
    return n;
  }

  function clearNode(node){
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function mutedNode(text, tag){
    return el(tag || 'div', {class:'ecap-muted', text:text || ''});
  }

  function pillNode(text, cls){
    return el('span', {class:`ecap-pill ${cls || ''}`.trim(), text:text || ''});
  }

  function listStatusNode(text){
    const item = el('div', {class:'ecap-item'});
    item.appendChild(el('span', {class:'ecap-muted', text:text || ''}));
    return item;
  }

  function setListStatus(host, text){
    if (!host) return;
    clearNode(host);
    host.appendChild(listStatusNode(text || 'Nothing yet.'));
  }


  function appendChildren(parent, children){
    (children || []).forEach((child)=>{
      if (child === null || child === undefined) return;
      if (Array.isArray(child)) return appendChildren(parent, child);
      if (typeof child === 'string' || typeof child === 'number') parent.appendChild(document.createTextNode(String(child)));
      else parent.appendChild(child);
    });
    return parent;
  }

  function h4Node(text, style){
    const h = el('h4', {text:text || ''});
    if (style) h.setAttribute('style', style);
    return h;
  }

  function cardNode(children, opts){
    const c = el(opts && opts.tag ? opts.tag : 'div', {class: opts && opts.className ? opts.className : 'ecap-card'});
    if (opts && opts.style) c.setAttribute('style', opts.style);
    appendChildren(c, children || []);
    return c;
  }

  function rowNode(children, style){
    const r = el('div', {class:'ecap-row'});
    if (style) r.setAttribute('style', style);
    appendChildren(r, children || []);
    return r;
  }

  function gridNode(className, children, style){
    const g = el('div', {class:className || 'ecap-grid2'});
    if (style) g.setAttribute('style', style);
    appendChildren(g, children || []);
    return g;
  }

  function hrNode(){ return el('div', {class:'ecap-hr'}); }

  function inputNode(id, placeholder, attrs){
    return el('input', Object.assign({id, placeholder: placeholder || ''}, attrs || {}));
  }

  function adminPasswordMeterNode(id){
    const wrap = el('div', {id, class:'ecap-passMeter', 'data-admin-password-meter':'1'});
    const top = el('div', {class:'ecap-passMeterTop'});
    top.appendChild(el('span', {text:'Password strength'}));
    top.appendChild(el('strong', {class:'ecap-passMeterLabel', text:'Start typing'}));
    const bar = el('div', {class:'ecap-passMeterBar'});
    bar.appendChild(el('span', {class:'ecap-passMeterFill'}));
    const rules = el('ul', {class:'ecap-passRules', 'aria-label':'Create-user password checklist'});
    [
      ['length', `At least ${ECAP_PASSWORD_MIN} characters`],
      ['recommended', `${ECAP_PASSWORD_RECOMMENDED}+ characters recommended`],
      ['context', 'Does not contain username, email name, or server name'],
      ['common', 'Not common or repetitive'],
      ['chars', `Spaces and symbols allowed; no control characters; max ${ECAP_PASSWORD_MAX} characters`]
    ].forEach(([key, text])=> rules.appendChild(el('li', {'data-pass-rule':key, text:'• ' + text})));
    wrap.appendChild(top);
    wrap.appendChild(bar);
    wrap.appendChild(rules);
    return wrap;
  }

  function textareaNode(id, placeholder, attrs){
    return el('textarea', Object.assign({id, placeholder: placeholder || ''}, attrs || {}));
  }

  function buttonNode(id, text, cls){
    return el('button', {id, class: cls || 'ecap-btn tight', type:'button', text:text || ''});
  }

  function optionNode(value, text){ return el('option', {value, text: text || value}); }

  function selectNode(id, options, attrs){
    const sel = el('select', Object.assign({id}, attrs || {}));
    (options || []).forEach((opt)=> sel.appendChild(Array.isArray(opt) ? optionNode(opt[0], opt[1]) : optionNode(opt, opt)));
    return sel;
  }

  function checkLabelNode(id, text, className, inputStyle){
    const label = el('label', {class: className || 'ecap-pill'});
    const checkbox = el('input', {id, type:'checkbox', style: inputStyle || 'width:auto'});
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(' ' + (text || '')));
    return label;
  }

  function statNode(label, id){
    const stat = el('div', {class:'ecap-stat'});
    stat.appendChild(el('div', {class:'lbl', text:label || ''}));
    stat.appendChild(el('div', {class:'val', id, text:'—'}));
    return stat;
  }

  function pillWithBoldNode(prefix, id, cls){
    const p = el('div', {class:`ecap-pill ${cls || ''}`.trim()});
    p.appendChild(document.createTextNode(prefix || ''));
    p.appendChild(el('b', {id, style:'font-weight:750', text:'—'}));
    return p;
  }

  function titledListCard(title, listId, opts){
    const list = el('div', {id:listId, class: opts && opts.listClass ? opts.listClass : 'ecap-list'});
    if (opts && opts.listStyle) list.setAttribute('style', opts.listStyle);
    return cardNode([h4Node(title), list], {className: opts && opts.cardClass ? opts.cardClass : 'ecap-card', style: opts && opts.cardStyle});
  }

  function titledInputCard(title, input){
    return cardNode([mutedNode(title), input], {style:'margin:0'});
  }

  function sectionHeroNode(category, title, description){
    return appendChildren(el('div', {class:'ecap-sectionHero'}), [
      el('div', {class:'eyebrow', text:category || 'Admin tools'}),
      h4Node(title || ''),
      mutedNode(description || '')
    ]);
  }

  function gotoButtonNode(tab, title, description){
    const b = el('button', {class:'ecap-btn ecap-gotoBtn', type:'button', 'data-ecap-goto':tab || 'dash'});
    b.appendChild(appendChildren(el('span'), [el('b', {text:title || tab || 'Open'}), el('span', {text:description || ''})]));
    return b;
  }

  function buildDashSection(host){
    clearNode(host);
    const overview = cardNode([
      h4Node('Overview'),
      gridNode('ecap-statGrid', [
        statNode('Online', 'ecapStatOnline'), statNode('Registered', 'ecapStatUsers'), statNode('Rooms', 'ecapStatRooms'),
        statNode('Sessions', 'ecapStatSessions'), statNode('Uptime', 'ecapStatUptime'), statNode('Postgres', 'ecapStatPg')
      ], 'margin-top:8px'),
      rowNode([
        pillWithBoldNode('Server time: ', 'ecapStatNow', 'warn'),
        pillWithBoldNode('Voice rooms: ', 'ecapVoiceRooms'),
        pillWithBoldNode('Voice users: ', 'ecapVoiceUsers')
      ], 'margin-top:10px'),
      gridNode('ecap-grid2', [
        cardNode([
          h4Node('Voice cap'),
          mutedNode('Default is 100. Use 30 for smaller rooms; 0 means unlimited. Lowering the cap disconnects users over the limit.'),
          rowNode([inputNode('ecapDashVoiceMax', '100 default, 30 smaller, 0 unlimited', {inputmode:'numeric'}), buttonNode('ecapDashVoiceApply', 'Apply', 'ecap-btn primary tight')], 'margin-top:10px')
        ], {style:'margin:0'}),
        cardNode([h4Node('Feature snapshot'), el('div', {id:'ecapFeaturePills', class:'ecap-actions'}), mutedNode('Edits are in the Settings tab (admin).')], {style:'margin:0'}),
        cardNode([
          h4Node('Admin panel map'),
          mutedNode('Quickly jump to the grouped tool area you need.'),
          gridNode('ecap-gotoGrid', [
            gotoButtonNode('users', 'People', 'users, sessions, account actions'),
            gotoButtonNode('rooms', 'Rooms', 'room locks, slowmode, broadcast'),
            gotoButtonNode('safety', 'Safety', 'incident mode and anti-abuse'),
            gotoButtonNode('voice', 'Voice', 'quality, noise canceling, push-to-talk'),
            gotoButtonNode('av', 'Media', 'webcam and media engine'),
            gotoButtonNode('settings', 'System', 'server settings, GIFs, display'),
            gotoButtonNode('audit', 'Audit', 'admin action history')
          ])
        ], {style:'margin:0'}),
        cardNode([
          rowNode([
            appendChildren(el('div'), [h4Node('Security dashboard', 'margin:0'), mutedNode('Encryption status, profile-field key health, Test Lab token protection, and privacy retention.')]),
            buttonNode('ecapSecurityRefresh', 'Refresh', 'ecap-btn tight')
          ], 'justify-content:space-between;align-items:center'),
          rowNode([el('span', {id:'ecapSecurityOverall', class:'ecap-pill warn', text:'security: —'}), el('span', {id:'ecapSecurityWhen', class:'ecap-pill', text:'checked: —'})], 'margin-top:10px;flex-wrap:wrap'),
          el('div', {id:'ecapSecurityChecks', class:'ecap-list compact', style:'margin-top:10px;max-height:210px'}),
          rowNode([buttonNode('ecapSecurityFinishSetup', 'Finish Security Setup', 'ecap-btn primary tight'), buttonNode('ecapSecurityRunRetention', 'Run privacy retention now', 'ecap-btn tight'), buttonNode('ecapSecurityEncryptProfiles', 'Encrypt old profile fields', 'ecap-btn tight'), buttonNode('ecapSecurityEncryptEmails', 'Encrypt old emails', 'ecap-btn tight'), buttonNode('ecapSecurityRotateProfiles', 'Rotate profile field key', 'ecap-btn tight'), buttonNode('ecapSecurityBackup', 'Create security backup', 'ecap-btn tight'), buttonNode('ecapSecurityRestoreBackup', 'Restore latest security backup', 'ecap-btn danger tight')], 'margin-top:10px;flex-wrap:wrap')
        ], {style:'margin:0'}),
        cardNode([
          h4Node('Admin Test Lab'),
          mutedNode('Open the live feature test suite in a separate admin-only tab. End users never receive this server-injected panel.'),
          rowNode([buttonNode('ecapOpenTestLab', 'Open Test Lab', 'ecap-btn primary tight')], 'margin-top:10px')
        ], {style:'margin:0'})
      ], 'margin-top:10px'),
      cardNode([
        rowNode([
          appendChildren(el('div'), [h4Node('Operations analytics', 'margin:0'), mutedNode('Trends from audit activity, sanctions, live room pressure, and admin actions.')]),
          buttonNode('ecapAnalyticsRefresh', 'Refresh analytics')
        ], 'justify-content:space-between;align-items:center'),
        el('div', {id:'ecapAnalyticsSummary', class:'ecap-trendGrid', style:'margin-top:10px'}),
        gridNode('ecap-grid2', [
          cardNode([appendChildren(el('div', {class:'ecap-chartHead'}), [appendChildren(el('div'), [el('div', {class:'title', text:'Audit activity'}), el('div', {class:'sub', text:'last 24 hours'})])]), el('div', {id:'ecapAuditChart', class:'ecap-barChart'})], {className:'ecap-chartCard'}),
          cardNode([appendChildren(el('div', {class:'ecap-chartHead'}), [appendChildren(el('div'), [el('div', {class:'title', text:'Sanctions created'}), el('div', {class:'sub', text:'last 7 days'})])]), el('div', {id:'ecapSanctionsChart', class:'ecap-barChart ecap-barChartTall'})], {className:'ecap-chartCard'})
        ], 'margin-top:10px'),
        gridNode('ecap-grid2', [
          titledListCard('Top actions (7d)', 'ecapTopActions', {cardStyle:'margin:0', listClass:'ecap-list compact', listStyle:'max-height:180px'}),
          titledListCard('Live room pressure', 'ecapTopRooms', {cardStyle:'margin:0', listClass:'ecap-list compact', listStyle:'max-height:180px'})
        ], 'margin-top:10px')
      ], {style:'margin-top:10px'}),
      cardNode([
        rowNode([
          appendChildren(el('div'), [h4Node('Diagnostics', 'margin:0'), mutedNode('Startup + on-demand preflight checks for DB, uploads, Socket.IO, room media, and config coherence.')]),
          buttonNode('ecapDiagRefresh', 'Run diagnostics')
        ], 'justify-content:space-between;align-items:center'),
        rowNode([el('div', {id:'ecapDiagOverall', class:'ecap-pill', text:'overall: —'}), el('div', {id:'ecapDiagWhen', class:'ecap-pill warn', text:'checked: —'}), el('div', {id:'ecapDiagSchema', class:'ecap-pill', text:'schema: —'})], 'margin-top:10px;flex-wrap:wrap'),
        el('div', {id:'ecapDiagChecks', class:'ecap-list', style:'margin-top:10px;max-height:220px'})
      ], {style:'margin-top:10px'})
    ]);
    const roster = cardNode([
      h4Node('Live roster'), mutedNode('Click a username to load details.'),
      el('div', {id:'ecapOnlineList', class:'ecap-actions', style:'margin-top:10px'}), hrNode(),
      appendChildren(el('div', {id:'ecapDrop', class:'ecap-drop'}), [
        el('div', {style:'font-weight:750', text:'🎯 Target user'}), mutedNode('Drag-and-drop a username from the UI, or type it below.'),
        appendChildren(el('div', {style:'margin-top:10px'}), ['Current: ', el('span', {id:'ecapTargetUser', style:'font-weight:780', text:'(none)'})]),
        rowNode([inputNode('ecapTargetInput', 'username'), buttonNode('ecapTargetLoad', 'Load', 'ecap-btn primary tight')], 'margin-top:10px')
      ])
    ]);
    const logCard = cardNode([h4Node('Admin log'), el('div', {class:'ecap-log'})]);
    appendChildren(host, [overview, roster, logCard]);
  }

  function buildModerationSection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Chat safety', 'Moderation command center', 'Review live sanctions, recent moderator actions, and jump into incident controls when needed.'),
      cardNode([h4Node('Moderation overview'), mutedNode('Live sanctions, recent moderation actions, and incident-mode status.'), hrNode(), el('div', {id:'ecapModSummary', class:'ecap-row'}), rowNode([el('div', {id:'ecapIncidentState', class:'ecap-pill', text:'Incident mode: —'}), buttonNode('ecapModerationRefresh', 'Refresh'), buttonNode('ecapOpenSafety', 'Open Safety')])]),
      gridNode('ecap-grid2', [titledListCard('Recent sanctions', 'ecapModerationSanctions', {cardClass:'ecap-card ecap-fill ecap-fillCol', listClass:'ecap-list ecap-fillScroll'}), titledListCard('Recent actions', 'ecapModerationActions', {cardClass:'ecap-card ecap-fill ecap-fillCol', listClass:'ecap-list ecap-fillScroll'})]),
      cardNode([
        h4Node('Profile post moderation'),
        mutedNode('Search profile posts, remove/restore posts, and remove comments from one admin surface.'),
        rowNode([inputNode('ecapProfilePostQuery', 'Search author/body/link…'), selectNode('ecapProfilePostStatus', [['active','active'], ['deleted','deleted'], ['all','all']], {class:'tight'}), buttonNode('ecapProfilePostRefresh', 'Search')], 'margin-top:10px;align-items:center'),
        rowNode([inputNode('ecapProfilePostReason', 'Moderation reason, used for remove/restore actions…')], 'margin-top:8px'),
        el('div', {id:'ecapProfilePostModerationList', class:'ecap-list', style:'margin-top:10px;max-height:360px'})
      ]),
      cardNode([
        h4Node('Profile reports queue'),
        mutedNode('Review user reports for profile posts/comments, warn the target user, dismiss the report, or remove the reported content.'),
        rowNode([inputNode('ecapProfileReportQuery', 'Search reporter, target, reason, details…'), selectNode('ecapProfileReportStatus', [['open','open'], ['actioned','actioned'], ['dismissed','dismissed'], ['all','all']], {class:'tight'}), buttonNode('ecapProfileReportRefresh', 'Refresh')], 'margin-top:10px;align-items:center'),
        rowNode([inputNode('ecapProfileReportReason', 'Admin note / warning text…')], 'margin-top:8px'),
        el('div', {id:'ecapProfileReportsList', class:'ecap-list', style:'margin-top:10px;max-height:360px'})
      ]),
      cardNode([
        h4Node('Profile badge tools'),
        mutedNode("Assign or remove profile badges shown on a user's public profile header."),
        rowNode([inputNode('ecapProfileBadgeUser', 'username'), inputNode('ecapProfileBadgeKey', 'badge key, e.g. verified'), inputNode('ecapProfileBadgeLabel', 'badge label, e.g. Verified'), buttonNode('ecapProfileBadgeLoad', 'Load')], 'margin-top:10px;align-items:center'),
        rowNode([inputNode('ecapProfileBadgeReason', 'reason / note'), buttonNode('ecapProfileBadgeAssign', 'Assign badge')], 'margin-top:8px'),
        el('div', {id:'ecapProfileBadgeList', class:'ecap-list', style:'margin-top:10px;max-height:260px'})
      ]),
      titledListCard('Operator notes', 'ecapModerationSuggestions')
    ]);
  }

  function buildUsersSection(host){
    clearNode(host);
    const userMode = selectNode('ecapUserMode', ['contains','prefix','exact','email','id'], {class:'tight', title:'Match mode'});
    const userStatus = selectNode('ecapUserStatus', [['any','any status'], 'active', 'suspended', 'deactivated', 'shadowbanned'], {class:'tight', title:'Account status'});
    const searchBar = appendChildren(el('div', {class:'ecap-userSearchBar'}), [
      inputNode('ecapUserQuery', 'Search username / email / id…', {autocomplete:'off', autocapitalize:'off', autocorrect:'off', spellcheck:'false', inputmode:'search'}),
      userMode,
      buttonNode('ecapUserSearchBtn', 'Search')
    ]);
    const userLimit = selectNode('ecapUserLimit', [['25','25/page'], ['50','50/page'], ['100','100/page']], {class:'tight', title:'Rows per page'});
    userLimit.value = '50';
    const filters = appendChildren(el('div', {class:'ecap-userFilterRow'}), [
      checkLabelNode('ecapUserOnlineOnly', 'online', 'ecap-pill tight'),
      checkLabelNode('ecapUserAdminsOnly', 'admins', 'ecap-pill tight'),
      userStatus,
      userLimit,
      el('span', {class:'ecap-muted ecap-userFilterHint', text:'Search or filter first. Results are paged.'})
    ]);
    const pager = appendChildren(el('div', {class:'ecap-userPager'}), [
      buttonNode('ecapUserPrevPage', 'Prev', 'ecap-btn tight'),
      el('span', {id:'ecapUserPageInfo', class:'ecap-muted', text:'No user query loaded'}),
      buttonNode('ecapUserNextPage', 'Next', 'ecap-btn tight')
    ]);
    const actionGrid = gridNode('ecap-grid2', [
      cardNode([h4Node('Session'), el('div', {id:'ecapActSession', class:'ecap-actions'})], {style:'margin:0'}),
      cardNode([h4Node('Account'), el('div', {id:'ecapActAccount', class:'ecap-actions'})], {style:'margin:0'}),
      cardNode([h4Node('Security'), el('div', {id:'ecapActSecurity', class:'ecap-actions'})], {style:'margin:0'}),
      cardNode([h4Node('Moderation'), el('div', {id:'ecapActMod', class:'ecap-actions'})], {style:'margin:0'})
    ], 'margin-top:8px');
    const details = cardNode([
      h4Node('Selected user'),
      rowNode([inputNode('ecapSelUser', 'username', {autocomplete:'off', autocapitalize:'off', autocorrect:'off', spellcheck:'false'}), buttonNode('ecapLoadUser', 'Load', 'ecap-btn primary tight')]),
      el('div', {id:'ecapUserSummary', class:'ecap-grid2'}), hrNode(), mutedNode('Actions'), actionGrid,
      mutedNode('Some actions require admin or specific permissions.')
    ]);
    details.lastChild.setAttribute('style', 'margin-top:10px');
    const timeline = cardNode([
      rowNode([appendChildren(el('div'), [h4Node('User activity timeline', 'margin:0'), mutedNode('Merged admin view of logins, sessions, room/message metadata, profile activity, reports, and moderation.')]), buttonNode('ecapUserTimelineRefresh', 'Refresh timeline')], 'justify-content:space-between;align-items:center'),
      rowNode([selectNode('ecapUserTimelineDays', [['7','7 days'], ['30','30 days'], ['90','90 days'], ['365','1 year']], {class:'tight'}), el('span', {id:'ecapUserTimelineMeta', class:'ecap-muted', text:'Load a user to view activity.'})], 'margin-top:10px;align-items:center'),
      el('div', {id:'ecapUserTimeline', class:'ecap-list compact', style:'margin-top:10px;max-height:420px'})
    ]);
    const create = cardNode([], {tag:'details', className:'ecap-card'});
    create.appendChild(el('summary', {style:'cursor:pointer;font-weight:750', text:'Create user'}));
    appendChildren(create, [mutedNode(`Requires admin. New accounts use the same username/password rules as public registration. Use a 4-8 digit password-recovery PIN. ${ECAP_PASSWORD_SUMMARY}`), rowNode([inputNode('ecapCreateUser', 'username', {autocomplete:'off', autocapitalize:'off', autocorrect:'off', spellcheck:'false', minlength:ECAP_USERNAME_MIN, maxlength:ECAP_USERNAME_MAX, pattern:ECAP_USERNAME_PATTERN, title:ECAP_USERNAME_TITLE}), inputNode('ecapCreateEmail', 'email')], 'margin-top:10px'), el('div', {id:'ecapCreateUserAvailability', class:'ecap-usernameAvailability', 'aria-live':'polite', text:'Enter a username to check availability.'}), rowNode([inputNode('ecapCreatePass', 'password / passphrase', {type:'password', minlength:ECAP_PASSWORD_MIN, maxlength:ECAP_PASSWORD_MAX, title:ECAP_PASSWORD_SUMMARY}), inputNode('ecapCreatePin', '4-8 digit Recovery PIN', {type:'password', inputmode:'numeric', maxlength:8, autocomplete:'new-password'}), checkLabelNode('ecapCreateIsAdmin', 'admin', 'ecap-pill tight'), buttonNode('ecapCreateBtn', 'Create', 'ecap-btn primary tight')]), adminPasswordMeterNode('ecapCreatePassMeter')]);
    create.children[1].setAttribute('style', 'margin-top:6px');
    appendChildren(host, [sectionHeroNode('People', 'Users and accounts', 'Search users first. Large servers do not load the full account list into the browser.'), cardNode([h4Node('User search'), searchBar, filters, pager, el('div', {id:'ecapUserResults', class:'ecap-list'})]), details, timeline, create]);
  }

  function buildRoomsSection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Chat spaces', 'Rooms and broadcasts', 'Manage room state, slowmode, room bans, and server-wide announcements from one place.'),
      cardNode([h4Node('Rooms'), rowNode([buttonNode('ecapRoomsReload', 'Reload'), inputNode('ecapRoomFilter', 'Filter rooms…')]), el('div', {id:'ecapRoomList', class:'ecap-list'})]),
      cardNode([
        rowNode([appendChildren(el('div'), [h4Node('Room radio stations', 'margin:0'), mutedNode('Add, edit, remove, and reorder the station presets shown in listening/radio rooms. Save writes to chat_rooms.json.')]), buttonNode('ecapRadioReload', 'Reload')], 'justify-content:space-between;align-items:center'),
        rowNode([selectNode('ecapRadioRoomSelect', []), buttonNode('ecapRadioAddStation', '+ Station', 'ecap-btn primary tight'), buttonNode('ecapRadioSave', 'Save stations', 'ecap-btn primary tight')], 'margin-top:10px'),
        mutedNode('Select a radio room, edit rows, then save. URLs must be HTTPS. Keep iHeart embeds as page URL plus embed URL when possible.'),
        el('div', {id:'ecapRadioStationList', class:'ecap-list', style:'margin-top:8px'}),
        el('div', {id:'ecapRadioStatus', class:'ecap-muted', style:'margin-top:8px'})
      ]),
      cardNode([h4Node('Kick / ban user from room'), rowNode([inputNode('ecapKRUser', 'username'), inputNode('ecapKRRoom', 'room')]), appendChildren(el('div', {class:'ecap-actions'}), [buttonNode('ecapKickBtn', 'Kick'), buttonNode('ecapRoomBanBtn', 'Room ban', 'ecap-btn danger tight')])]),
      cardNode([h4Node('Broadcast'), textareaNode('ecapBroadcast', 'Global announcement…'), rowNode([buttonNode('ecapBroadcastBtn', 'Send broadcast', 'ecap-btn primary tight')])])
    ]);
  }

  function buildVoiceSection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Echo Voice', 'Voice settings', 'Control custom Echo Voice quality, mic processing, push-to-talk defaults, and room voice limits from one place.'),
      cardNode([
        rowNode([appendChildren(el('div'), [h4Node('Voice service', 'margin:0'), mutedNode('These settings affect the custom Echo Voice API/client. Users may need to reload or reconnect voice for changes to fully apply.')]), buttonNode('ecapVoiceSettingsReload', 'Reload')], 'justify-content:space-between;align-items:center'),
        el('div', {id:'ecapVoiceSummary', class:'ecap-muted', text:'Loading current voice settings…'}),
        gridNode('ecap-grid2', [
          titledInputCard('Room voice cap', inputNode('ecapVoiceMax', '100 default, 30 smaller, 0 unlimited', {inputmode:'numeric'})),
          cardNode([mutedNode('Voice enabled'), checkLabelNode('ecapVoiceEnabled', 'allow Echo Voice features', 'ecap-pill tight')], {style:'margin:0'}),
          cardNode([mutedNode('Voice quality'), selectNode('ecapVoiceQuality', [['low','Low - lower bandwidth'], ['balanced','Balanced - recommended'], ['high','High - best quality']])], {style:'margin:0'}),
          cardNode([mutedNode('Automatic quality'), checkLabelNode('ecapVoiceAutoQuality', 'auto-adjust quality during rough connections', 'ecap-pill tight')], {style:'margin:0'})
        ], 'margin-top:10px')
      ]),
      cardNode([
        h4Node('Microphone processing'),
        mutedNode('Noise canceling uses browser audio constraints when supported. It is safe to leave on for most users.'),
        gridNode('ecap-grid2', [
          checkLabelNode('ecapVoiceNoise', 'noise canceling / noise suppression', 'ecap-pill tight'),
          checkLabelNode('ecapVoiceEcho', 'echo cancellation', 'ecap-pill tight'),
          checkLabelNode('ecapVoiceGain', 'automatic gain control', 'ecap-pill tight'),
          checkLabelNode('ecapVoicePttDefault', 'push-to-talk by default', 'ecap-pill tight')
        ], 'margin-top:10px'),
        mutedNode('Users still control their own microphone permission. Push-to-talk keeps mic tracks muted unless the user holds Talk or checks Hands-free.')
      ], {style:'margin-top:10px'}),
      cardNode([
        h4Node('STUN/TURN connectivity'),
        mutedNode('STUN is fine for many LAN tests. TURN is the relay that makes webcam, voice, and P2P files work through cellular, hotel Wi-Fi, corporate firewalls, and strict NAT.'),
        el('div', {id:'ecapIceSummary', class:'ecap-muted', style:'margin-top:8px', text:'Loading ICE status…'}),
        gridNode('ecap-grid2', [
          titledInputCard('P2P/WebRTC ICE servers', textareaNode('ecapIceP2p', 'stun:stun.l.google.com:19302\nturn:turn.example.com:3478')),
          titledInputCard('Voice/webcam ICE servers', textareaNode('ecapIceVoice', 'blank = use P2P/WebRTC ICE servers'))
        ], 'margin-top:10px'),
        gridNode('ecap-grid2', [
          titledInputCard('TURN username', inputNode('ecapIceTurnUsername', 'optional')),
          titledInputCard('TURN credential', inputNode('ecapIceTurnCredential', 'blank keeps secret out of saved config', {type:'password'}))
        ], 'margin-top:10px'),
        mutedNode('For production, prefer env/secret-managed short-lived TURN credentials. Static credentials are mainly for local or private testing.'),
        rowNode([buttonNode('ecapIceReload', 'Reload ICE'), buttonNode('ecapIceApply', 'Save ICE servers', 'ecap-btn primary tight')], 'margin-top:10px')
      ], {style:'margin-top:10px'}),
      cardNode([
        h4Node('End-user controls'),
        mutedNode('Room chat, private-message windows, and group-chat windows now expose a Talk button plus a Hands-free checkbox. The checkbox lets users keep their mic open instead of holding Talk.'),
        rowNode([buttonNode('ecapVoiceSettingsApply', 'Save voice settings', 'ecap-btn primary tight')], 'margin-top:10px')
      ], {style:'margin-top:10px'})
    ]);
  }

  function buildAvSection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Echo Media', 'Webcam and built-in WebRTC', 'Manage the built-in Echo webcam layer. This controls browser capture quality, codec preference, and camera policy.'),
      cardNode([
        rowNode([appendChildren(el('div'), [h4Node('Room media mode', 'margin:0'), mutedNode('Echo mode enables webcam controls over the built-in WebRTC mesh. Standard mode keeps room voice available but disables webcam controls.')]), buttonNode('ecapMediaRefresh', 'Refresh')], 'justify-content:space-between;align-items:center'),
        gridNode('ecap-statGrid', [statNode('Requested', 'ecapAvRequested'), statNode('Active', 'ecapAvActive'), statNode('Webcam', 'ecapAvWebcam'), statNode('Transport', 'ecapMediaTransport')], 'margin-top:10px'),
        rowNode([selectNode('ecapAvModeSelect', [['echo','Echo built-in voice + webcam'], ['standard','Standard voice only']]), buttonNode('ecapMediaApply', 'Apply media settings', 'ecap-btn primary tight')], 'margin-top:10px'),
        mutedNode('Clients should reload after server-wide media changes so their startup config and GUI match the latest policy.')
      ]),
      cardNode([
        h4Node('Webcam quality defaults'),
        mutedNode('The server default applies to new sessions. Users can still lower quality locally in the webcam panel unless you later add forced policy.'),
        gridNode('ecap-grid3', [
          titledInputCard('Default webcam quality', selectNode('ecapWebcamQuality', [['low','Low - sharper low data'], ['balanced','Balanced - recommended'], ['high','High - 720p']]), {style:'margin:0'}),
          titledInputCard('Codec preference', selectNode('ecapWebcamCodecStrategy', [['prefer-compatible','Prefer compatibility'], ['prefer-efficient','Prefer efficient codecs'], ['prefer-quality','Prefer quality']]), {style:'margin:0'}),
          cardNode([mutedNode('Webcam enabled'), checkLabelNode('ecapWebcamEnabled', 'allow room webcams', 'ecap-pill tight')], {style:'margin:0'})
        ], 'margin-top:10px'),
        mutedNode('Low quality improves appearance by reducing capture resolution/FPS before bitrate, which usually looks cleaner than crushing a high-res stream.')
      ], {style:'margin-top:10px'}),
      cardNode([
        h4Node('Webcam policy'),
        mutedNode('Controls camera privacy and viewer limits at the Echo app layer. Owner approval is safest for public rooms.'),
        gridNode('ecap-grid3', [
          titledInputCard('Approval mode', selectNode('ecapWebcamPolicy', [['owner_approval','Owner approval'], ['open','Open to room'], ['disabled','Disable webcam']])),
          titledInputCard('Max viewers per webcam', inputNode('ecapWebcamMaxViewers', '0 = unlimited', {inputmode:'numeric'})),
          titledInputCard('Default media policy', selectNode('ecapDefaultMediaPolicy', [['user_choice','User choice'], ['voice_first','Voice first'], ['webcam_first','Webcam first'], ['both_first','Both first']]))
        ], 'margin-top:10px'),
        mutedNode('Default policy never bypasses browser permission prompts. Users still click a media button and approve camera/mic access.')
      ], {style:'margin-top:10px'}),
      cardNode([h4Node('Media diagnostics'), mutedNode('Use the browser diagnostics page to test camera permission, ICE state, selected candidate type, and TURN relay behavior from this exact browser.'), rowNode([buttonNode('ecapOpenWebrtcDiag', 'Open WebRTC diagnostics', 'ecap-btn primary tight')], 'margin-top:8px'), el('div', {id:'ecapMediaPills', class:'ecap-actions', style:'margin-top:10px'}), el('div', {id:'ecapMediaChecks', class:'ecap-list compact', style:'margin-top:10px;max-height:360px'})], {style:'margin-top:10px'})
    ]);
  }

  function buildSettingsSection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Server', 'System settings and service integrations', 'Change server-wide behavior here. Settings are grouped by purpose so display, limits, cleanup, uploads, and integrations do not blend together.'),
      cardNode([rowNode([appendChildren(el('div'), [h4Node('Admin analytics snapshot', 'margin:0'), mutedNode('Top actors, affected areas, and action mix from the last 7 days. Opaque ids are grouped so this snapshot stays readable.')]), el('div', {id:'ecapAnalyticsGeneratedAt', class:'ecap-pill warn', text:'generated: —'})], 'justify-content:space-between;align-items:center'), gridNode('ecap-grid2', [titledListCard('Top actors (7d)', 'ecapTopActors', {cardStyle:'margin:0', listClass:'ecap-list compact', listStyle:'max-height:180px'}), titledListCard('Top affected areas (7d)', 'ecapTopTargets', {cardStyle:'margin:0', listClass:'ecap-list compact', listStyle:'max-height:180px'})], 'margin-top:10px')]),
      cardNode([h4Node('System settings (admin)'), mutedNode('General server/runtime toggles. Some changes may require clients to reload or a server restart.'), hrNode(), el('div', {id:'ecapSettingsSummary', class:'ecap-settingsSummary'}), el('div', {id:'ecapSettingsForm', class:'ecap-settingsGroups'}), hrNode(), rowNode([buttonNode('ecapSettingsReload', 'Reload'), buttonNode('ecapSettingsApply', 'Apply', 'ecap-btn primary tight')])]),
      cardNode([h4Node('GIFs (GIPHY) (admin)'), mutedNode('GIF search uses a server-side proxy. If the key is missing, the GIF modal shows an error. The key is stored in the server settings file.'), hrNode(), gridNode('ecap-grid2', [cardNode([mutedNode('API key'), rowNode([inputNode('ecapGiphyKey', 'Paste GIPHY key…', {type:'password'}), buttonNode('ecapGiphyShow', 'Show')], 'margin-top:10px'), appendChildren(mutedNode('Status: ', 'div'), [el('b', {id:'ecapGiphyKeyStatus', style:'font-weight:780', text:'—'})])], {style:'margin:0'}), cardNode([mutedNode('Search policy'), rowNode([inputNode('ecapGiphyRating', 'pg-13'), inputNode('ecapGiphyLang', 'en'), inputNode('ecapGiphyLimit', '24', {inputmode:'numeric'})], 'margin-top:10px'), mutedNode('rating / language / default limit')], {style:'margin:0'})]), hrNode(), rowNode([buttonNode('ecapGiphyReload', 'Reload'), buttonNode('ecapGiphyApply', 'Apply', 'ecap-btn primary tight')])], {style:'margin-top:10px'})
    ]);
  }

  function buildSafetySection(host){
    clearNode(host);
    appendChildren(host, [
      sectionHeroNode('Protection', 'Safety and anti-abuse', 'Use this area for raid response, rate-limit tuning, and abuse controls that affect the whole server.'),
      cardNode([h4Node('Incident mode'), mutedNode('Apply a preset for abuse spikes or raids. Runtime changes happen immediately; persistence is optional.'), hrNode(), rowNode([selectNode('ecapIncidentPreset', ['soft_lockdown', 'hard_lockdown', 'raid_mode', 'silent_observe']), checkLabelNode('ecapIncidentPersist', 'persist', 'ecap-pill', 'width:auto;margin-right:6px'), buttonNode('ecapIncidentApply', 'Apply', 'ecap-btn primary tight'), buttonNode('ecapIncidentDisable', 'Disable')]), rowNode([el('div', {id:'ecapIncidentStatus', class:'ecap-pill', text:'Incident mode: —'})])]),
      cardNode([h4Node('IP ban'), mutedNode('Blocks a normalized IPv4/IPv6 address and revokes matching active auth sessions/tokens. Your current admin IP is protected from self-ban.'), hrNode(), gridNode('ecap-grid2', [inputNode('ecapBanIpAddress', 'IP address'), inputNode('ecapBanIpReason', 'Reason')]), hrNode(), rowNode([buttonNode('ecapBanIpBtn', 'Ban IP', 'ecap-btn danger tight')])], {style:'margin-top:10px'}),
      cardNode([h4Node('Anti-abuse (admin)'), mutedNode('Updates apply immediately on server. Be careful with very low windows/limits.'), hrNode(), el('div', {id:'ecapAntiForm', class:'ecap-grid2'}), hrNode(), rowNode([buttonNode('ecapAntiReload', 'Reload'), buttonNode('ecapAntiApply', 'Apply', 'ecap-btn primary tight')])], {style:'margin-top:10px'})
    ]);
  }

  function buildRolesSection(host){
    clearNode(host);
    appendChildren(host, [sectionHeroNode('Permissions', 'Roles and effective access', 'Create roles, clone permission sets, and inspect exactly what a selected user can do.'), gridNode('ecap-grid3', [
      cardNode([h4Node('Roles'), rowNode([inputNode('ecapNewRoleName', 'new role name'), buttonNode('ecapRoleCreate', 'Create')]), rowNode([selectNode('ecapRoleCloneSource', []), inputNode('ecapRoleCloneName', 'clone name'), buttonNode('ecapRoleClone', 'Clone')]), el('div', {id:'ecapRolesList', class:'ecap-list ecap-fillScroll'})], {className:'ecap-card ecap-fill ecap-fillCol'}),
      cardNode([h4Node('Permissions'), el('div', {id:'ecapRolesCurrentRole', class:'ecap-muted', text:'Select a role to inspect or edit.'}), el('div', {id:'ecapRolePermissions', class:'ecap-list ecap-fillScroll'})], {className:'ecap-card ecap-fill ecap-fillCol'}),
      cardNode([h4Node('User inspector'), rowNode([inputNode('ecapRolesUser', 'username'), buttonNode('ecapRolesLoadUser', 'Load')]), rowNode([selectNode('ecapExplainPermission', []), buttonNode('ecapExplainPermissionBtn', 'Explain')]), el('div', {id:'ecapPermissionExplain', class:'ecap-muted', text:'Load a user, then explain a specific permission.'}), el('div', {id:'ecapRolesUserSummary', class:'ecap-list'}), el('div', {id:'ecapRolesUserPermissions', class:'ecap-list ecap-fillScroll'})], {className:'ecap-card ecap-fill ecap-fillCol'})
    ])]);
  }

  function buildAuditSection(host){
    clearNode(host);
    appendChildren(host, [sectionHeroNode('Audit', 'Admin activity history', 'Filter by actor, action, target, or detail text when you need to track what changed.'), cardNode([h4Node('Audit log'), gridNode('ecap-grid2', [inputNode('ecapAuditQ', 'General filter (actor, action, target, details)…'), inputNode('ecapAuditActor', 'actor filter'), inputNode('ecapAuditAction', 'action filter'), inputNode('ecapAuditTarget', 'target filter')]), rowNode([buttonNode('ecapAuditRefresh', 'Refresh')]), el('div', {id:'ecapAuditList', class:'ecap-list ecap-fillScroll'})], {className:'ecap-card ecap-fill ecap-fillCol', style:'gap:8px'})]);
  }

  function textBlock(lines){
    const wrap = el('div');
    wrap.style.minWidth = '0';
    (lines || []).forEach((line, idx)=>{
      const d = el('div', {text: line && line.text !== undefined ? line.text : ''});
      if (line && line.className) d.className = line.className;
      if (line && line.style) d.setAttribute('style', line.style);
      if (idx === 0 && !(line && line.style)) d.style.fontWeight = '750';
      wrap.appendChild(d);
    });
    return wrap;
  }

  function safe(s){
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function panelToast(type, msg, meta, ms){
    const st = document.getElementById('ecapToastStack');
    if (!st) return;
    const t = el('div', {class:`ecap-toast ${type||''}`, role:'status', 'aria-live':'polite'});
    const wrap = el('div');
    wrap.style.minWidth = '0';
    const msgEl = el('div', {class:'tmsg'});
    msgEl.style.fontWeight = '700';
    msgEl.style.whiteSpace = 'nowrap';
    msgEl.style.overflow = 'hidden';
    msgEl.style.textOverflow = 'ellipsis';
    msgEl.textContent = String(msg || '');
    const metaEl = el('div', {class:'tmeta'});
    metaEl.style.whiteSpace = 'nowrap';
    metaEl.style.overflow = 'hidden';
    metaEl.style.textOverflow = 'ellipsis';
    metaEl.textContent = String(meta || '');
    wrap.appendChild(msgEl);
    wrap.appendChild(metaEl);
    const close = el('button', {class:'x', type:'button', title:'Dismiss notification', 'aria-label':'Dismiss admin notification', text:'✕'});
    close.addEventListener('click', ()=>t.remove());
    t.appendChild(wrap);
    t.appendChild(close);
    st.prepend(t);
    const ttl = (ms === undefined || ms === null) ? 3200 : ms;
    if (ttl > 0) setTimeout(()=>{ try{ t.remove(); }catch(_){ } }, ttl);
  }

  function toast(type, msg, meta, ms){
    panelToast(type, msg, meta, ms);
  }

  function ensureAdminPasswordModal(panel){
    const host = panel || ensurePanel();
    if (!host) return null;
    let modal = host.querySelector('.ecap-modalBackdrop[data-modal="admin-reauth"]');
    if (modal) return modal;
    modal = el('div', {class:'ecap-modalBackdrop', 'data-modal':'admin-reauth', 'aria-hidden':'true'});
    const box = el('div', {class:'ecap-modal', role:'dialog', 'aria-modal':'true', 'aria-labelledby':'ecapAdminReauthTitle'});
    const form = el('form', {novalidate:''});
    form.appendChild(el('div', {class:'ecap-modalTitle', id:'ecapAdminReauthTitle', text:'Admin password confirmation'}));
    form.appendChild(el('div', {class:'ecap-modalText', text:'Confirm your password once to unlock admin actions for this login session.'}));
    form.appendChild(el('label', {class:'ecap-fieldLabel', for:'ecapAdminReauthPassword', text:'Current password'}));
    form.appendChild(el('input', {id:'ecapAdminReauthPassword', name:'current_password', type:'password', autocomplete:'current-password', spellcheck:'false'}));
    form.appendChild(el('div', {class:'ecap-errorText', 'aria-live':'polite'}));
    form.appendChild(el('div', {class:'ecap-busyNote', 'aria-live':'polite'}));
    const actions = el('div', {class:'ecap-modalActions'});
    actions.appendChild(el('button', {type:'button', class:'ecap-btn', 'data-act':'cancel', text:'Cancel'}));
    actions.appendChild(el('button', {type:'submit', class:'ecap-btn primary', 'data-act':'confirm', text:'Confirm'}));
    form.appendChild(actions);
    box.appendChild(form);
    modal.appendChild(box);
    modal.addEventListener('pointerdown', (e)=>{
      if (e.target === modal) e.preventDefault();
    });
    host.appendChild(modal);
    return modal;
  }



  function adminDialog(opts){
    const host = ensurePanel();
    if (!host) return Promise.resolve(null);
    const options = opts || {};
    const priorFocus = document.activeElement;
    return new Promise((resolve)=>{
      const dialogId = `ecapDialogTitle_${Date.now()}_${Math.random().toString(16).slice(2)}`;
      const descId = `ecapDialogDesc_${Date.now()}_${Math.random().toString(16).slice(2)}`;
      const modal = el('div', {class:'ecap-modalBackdrop open', 'data-modal':'admin-action', 'aria-hidden':'false'});
      const box = el('div', {class:'ecap-modal', role:'dialog', 'aria-modal':'true', 'aria-labelledby':dialogId});
      if (options.message) box.setAttribute('aria-describedby', descId);
      if (options.danger) box.classList.add('danger');
      const form = el('form', {novalidate:''});
      form.appendChild(el('div', {class:'ecap-modalTitle', id:dialogId, text: options.title || 'Admin action'}));
      if (options.message) form.appendChild(el('div', {class:'ecap-modalText', id:descId, text: options.message}));
      const fieldsWrap = el('div', {class:'ecap-modalFields'});
      const controls = [];
      (options.fields || []).forEach((field)=>{
        const f = field || {};
        const name = f.name || 'value';
        const id = `ecapDialog_${Date.now()}_${Math.random().toString(16).slice(2)}_${name}`;
        fieldsWrap.appendChild(el('label', {class:'ecap-fieldLabel', for:id, text:f.label || name}));
        let input;
        if (f.type === 'textarea'){
          input = el('textarea', {id, name, placeholder:f.placeholder || ''});
        } else if (f.type === 'select'){
          input = el('select', {id, name});
          (f.options || []).forEach((opt)=>{
            if (Array.isArray(opt)) input.appendChild(optionNode(opt[0], opt[1]));
            else input.appendChild(optionNode(opt, opt));
          });
        } else {
          input = el('input', {id, name, type:f.type || 'text', placeholder:f.placeholder || ''});
          if (f.inputmode) input.setAttribute('inputmode', f.inputmode);
          if (f.autocomplete) input.setAttribute('autocomplete', f.autocomplete);
          if (f.maxlength) input.setAttribute('maxlength', String(f.maxlength));
        }
        if (f.required) input.setAttribute('required', 'required');
        if (f.defaultValue !== undefined && f.defaultValue !== null) input.value = String(f.defaultValue);
        fieldsWrap.appendChild(input);
        controls.push({field:f, input});
      });
      if (controls.length) form.appendChild(fieldsWrap);
      const error = el('div', {class:'ecap-errorText', 'aria-live':'polite'});
      form.appendChild(error);
      const actions = el('div', {class:'ecap-modalActions'});
      const cancelBtn = el('button', {type:'button', class:'ecap-btn', text:options.cancelText || 'Cancel'});
      const okBtn = el('button', {type:'submit', class:`ecap-btn ${options.danger ? 'danger' : 'primary'}`, text:options.confirmText || 'Continue'});
      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      form.appendChild(actions);
      box.appendChild(form);
      modal.appendChild(box);
      let closed = false;
      const close = (value)=>{
        if (closed) return;
        closed = true;
        document.removeEventListener('keydown', onKey);
        try{ modal.remove(); }catch(_){ }
        try{
          if (priorFocus && typeof priorFocus.focus === 'function') priorFocus.focus();
        }catch(_){ }
        resolve(value);
      };
      const onKey = (e)=>{
        if (e.key === 'Escape'){
          e.preventDefault();
          close(null);
        }
      };
      cancelBtn.addEventListener('click', ()=>close(null));
      modal.addEventListener('pointerdown', (e)=>{
        if (e.target === modal) e.preventDefault();
      });
      form.addEventListener('submit', (e)=>{
        e.preventDefault();
        const values = {};
        for (const item of controls){
          const f = item.field || {};
          const name = f.name || 'value';
          let v = item.input.value;
          if (f.trim !== false) v = String(v || '').trim();
          if (f.required && !v){
            error.textContent = `${f.label || name} is required.`;
            item.input.focus();
            return;
          }
          values[name] = v;
        }
        if (typeof options.validate === 'function'){
          const msg = options.validate(values);
          if (msg){ error.textContent = String(msg); return; }
        }
        close(controls.length ? values : true);
      });
      host.appendChild(modal);
      document.addEventListener('keydown', onKey);
      setTimeout(()=>{
        const first = controls.length ? controls[0].input : okBtn;
        try{ first.focus(); if (controls.length && first.select) first.select(); }catch(_){ }
      }, 0);
    });
  }

  async function adminConfirm(title, message, opts){
    return !!(await adminDialog(Object.assign({title, message, fields:[], confirmText:'Confirm'}, opts || {})));
  }

  async function adminPrompt(title, message, field, opts){
    const values = await adminDialog(Object.assign({title, message, fields:[Object.assign({name:'value'}, field || {})]}, opts || {}));
    return values ? values.value : null;
  }

  async function adminPromptFields(title, message, fields, opts){
    return await adminDialog(Object.assign({title, message, fields:fields || []}, opts || {}));
  }

  function buildPanel(){
    const panel = el('div', {id:'ecAdminPanel'});
    if (!adminStartupUnlocked) panel.classList.add('ecap-startup-locked');
    if (state.max && !state.mini) panel.classList.add('ecap-max');
    if (state.mini) panel.classList.add('ecap-mini');
    if (state.pinned) panel.classList.add('ecap-pinned');

    // Restore position only if not pinned.
    if (!state.pinned && state.left !== undefined && state.top !== undefined){
      panel.style.left = `${state.left}px`;
      panel.style.top = `${state.top}px`;
      panel.style.right = 'auto';
    }
    restorePanelSize(panel);

    const head = el('div', {class:'ecap-head'});

    const titleRow = el('div', {class:'ecap-titleRow'});
    const dot = el('div', {class:'ecap-dot', title:'API status'});
    const titleBlock = el('div', {class:'ecap-titleBlock'});
    const title = el('div', {class:'ecap-title', text:SERVER_ADMIN_NAME});
    const subtitle = el('div', {class:'ecap-subtitle', text:`${SERVER_NAME} admin-only controls (RBAC + JWT)`});
    titleBlock.appendChild(title);
    titleBlock.appendChild(subtitle);
    titleRow.appendChild(dot);
    titleRow.appendChild(titleBlock);

    const btns = el('div', {class:'ecap-headBtns'});
    const btnRefresh = el('button', {class:'ecap-iconBtn', title:'Refresh', 'aria-label':'Refresh admin panel', text:'⟳'});
    const btnPin = el('button', {class:'ecap-iconBtn', title:'Pin/Unpin', 'aria-label':'Pin or unpin admin panel', text:'📌'});
    const btnMax = el('button', {class:'ecap-iconBtn', title:'Maximize', 'aria-label':'Maximize admin panel', text:'⛶'});
    const btnMini = el('button', {class:'ecap-iconBtn', title:'Minimize', 'aria-label':'Minimize admin panel', text:'▁'});
    const btnClose = el('button', {class:'ecap-iconBtn danger', title:'Close', 'aria-label':'Close admin panel', text:'✕'});
    btns.appendChild(btnRefresh);
    btns.appendChild(btnPin);
    btns.appendChild(btnMax);
    btns.appendChild(btnMini);
    btns.appendChild(btnClose);

    head.appendChild(titleRow);
    head.appendChild(btns);

    const body = el('div', {class:'ecap-body'});
    const toastStack = el('div', {class:'ecap-toastStack', id:'ecapToastStack'});
    body.appendChild(toastStack);

    const tabs = el('div', {class:'ecap-tabs ecap-navShell'});
    tabs.appendChild(appendChildren(el('div', {class:'ecap-navHead'}), [
      appendChildren(el('div'), [el('div', {class:'ecap-navTitle', text:'Admin workspace'}), el('div', {class:'ecap-navHint', text:'Grouped by job: monitor, people, chat, protection, and server.'})]),
      el('div', {class:'ecap-pill warn', text:'admin only'})
    ]));
    const tabGroups = [
      ['Monitor', [['dash','📊','Overview'], ['audit','🧾','Audit']]],
      ['People', [['users','👤','Users'], ['roles','🧩','Roles']]],
      ['Chat', [['moderation','🛡️','Moderation'], ['rooms','🏷️','Rooms']]],
      ['Protection', [['safety','🚨','Safety']]],
      ['Server', [['voice','🎙️','Voice'], ['av','📹','Media'], ['settings','⚙️','System']]]
    ];
    const tabNames = tabGroups.flatMap(group => group[1]);
    const tabEls = {};
    const tabGroupWrap = el('div', {class:'ecap-tabGroups', role:'tablist', 'aria-label':'Admin workspace tabs'});
    for (const [groupLabel, groupTabs] of tabGroups){
      const group = el('div', {class:'ecap-tabGroup'});
      group.appendChild(el('div', {class:'ecap-tabGroupTitle', text:groupLabel}));
      const btnWrap = el('div', {class:'ecap-tabGroupBtns'});
      for (const [key, ico, label] of groupTabs){
        const t = el('button', {class:'ecap-tab', type:'button', id:`ecapTab-${key}`, role:'tab', 'aria-selected':'false', 'aria-controls':`ecapSec-${key}`});
        t.appendChild(el('span', {class:'ico', text:ico}));
        t.appendChild(el('span', {text:label}));
        btnWrap.appendChild(t);
        tabEls[key] = t;
      }
      group.appendChild(btnWrap);
      tabGroupWrap.appendChild(group);
    }
    tabs.appendChild(tabGroupWrap);

    const secDash = el('div', {class:'ecap-section', 'data-sec':'dash', id:'ecapSec-dash', role:'tabpanel', 'aria-labelledby':'ecapTab-dash'});
    const secModeration = el('div', {class:'ecap-section', 'data-sec':'moderation', id:'ecapSec-moderation', role:'tabpanel', 'aria-labelledby':'ecapTab-moderation'});
    const secUsers = el('div', {class:'ecap-section', 'data-sec':'users', id:'ecapSec-users', role:'tabpanel', 'aria-labelledby':'ecapTab-users'});
    const secRooms = el('div', {class:'ecap-section', 'data-sec':'rooms', id:'ecapSec-rooms', role:'tabpanel', 'aria-labelledby':'ecapTab-rooms'});
    const secVoice = el('div', {class:'ecap-section', 'data-sec':'voice', id:'ecapSec-voice', role:'tabpanel', 'aria-labelledby':'ecapTab-voice'});
    const secAv = el('div', {class:'ecap-section', 'data-sec':'av', id:'ecapSec-av', role:'tabpanel', 'aria-labelledby':'ecapTab-av'});
    const secSafety = el('div', {class:'ecap-section', 'data-sec':'safety', id:'ecapSec-safety', role:'tabpanel', 'aria-labelledby':'ecapTab-safety'});
    const secRoles = el('div', {class:'ecap-section', 'data-sec':'roles', id:'ecapSec-roles', role:'tabpanel', 'aria-labelledby':'ecapTab-roles'});
    const secSettings = el('div', {class:'ecap-section', 'data-sec':'settings', id:'ecapSec-settings', role:'tabpanel', 'aria-labelledby':'ecapTab-settings'});
    const secAudit = el('div', {class:'ecap-section', 'data-sec':'audit', id:'ecapSec-audit', role:'tabpanel', 'aria-labelledby':'ecapTab-audit'});

    body.appendChild(tabs);
    body.appendChild(secDash);
    body.appendChild(secModeration);
    body.appendChild(secUsers);
    body.appendChild(secRooms);
    body.appendChild(secVoice);
    body.appendChild(secAv);
    body.appendChild(secSafety);
    body.appendChild(secRoles);
    body.appendChild(secSettings);
    body.appendChild(secAudit);

    panel.appendChild(head);
    panel.appendChild(body);
    document.body.appendChild(panel);
    panelRef = panel;

    function setTab(key){
      for (const [k, t] of Object.entries(tabEls)){
        const active = k === key;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', active ? 'true' : 'false');
        t.tabIndex = active ? 0 : -1;
      }
      const sections = {dash:secDash, moderation:secModeration, users:secUsers, rooms:secRooms, voice:secVoice, av:secAv, safety:secSafety, roles:secRoles, settings:secSettings, audit:secAudit};
      for (const [k, section] of Object.entries(sections)){
        const active = k === key;
        section.classList.toggle('active', active);
        section.hidden = !active;
        section.setAttribute('aria-hidden', active ? 'false' : 'true');
      }
      state.tab = key;
      saveState();
    }
    for (const [k,t] of Object.entries(tabEls)) t.addEventListener('click', ()=>setTab(k));
    tabGroupWrap.addEventListener('keydown', (e)=>{
      const keys = ['ArrowRight','ArrowDown','ArrowLeft','ArrowUp','Home','End'];
      if (!keys.includes(e.key)) return;
      const current = e.target && e.target.closest ? e.target.closest('.ecap-tab') : null;
      if (!current) return;
      const ordered = tabNames.map(([key]) => tabEls[key]).filter(Boolean);
      const idx = ordered.indexOf(current);
      if (idx < 0) return;
      e.preventDefault();
      let nextIdx = idx;
      if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = ordered.length - 1;
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') nextIdx = (idx - 1 + ordered.length) % ordered.length;
      else nextIdx = (idx + 1) % ordered.length;
      const next = ordered[nextIdx];
      if (next){
        const nextKey = Object.keys(tabEls).find(k => tabEls[k] === next);
        if (nextKey) setTab(nextKey);
        try{ next.focus(); }catch(_){ }
      }
    });
    body.addEventListener('click', (e)=>{
      const btn = e.target && e.target.closest ? e.target.closest('[data-ecap-goto]') : null;
      if (!btn) return;
      const key = btn.getAttribute('data-ecap-goto') || 'dash';
      if (!tabEls[key]) return;
      e.preventDefault();
      setTab(key);
    });
    setTab(state.tab || 'dash');

    // Buttons: stop drag initiation
    [btnRefresh, btnPin, btnMax, btnMini, btnClose].forEach(b=>{
      b.addEventListener('pointerdown', e=>e.stopPropagation());
    });

    btnMini.addEventListener('click', ()=>{
      const willMini = !panel.classList.contains('ecap-mini');
      if (willMini){
        // If we're maximized, collapse cleanly (otherwise you get a huge blank box).
        state._preMiniWasMax = panel.classList.contains('ecap-max');
        if (state._preMiniWasMax){
          panel.classList.remove('ecap-max');
          state.max = false;
        }
        panel.classList.add('ecap-mini');
        // Ensure compact height even if other rules linger.
        panel.style.height = '52px';
        panel.style.maxHeight = '52px';
      } else {
        panel.classList.remove('ecap-mini');
        panel.style.height = '';
        panel.style.maxHeight = '';
        if (state._preMiniWasMax){
          panel.classList.add('ecap-max');
          state.max = true;
        } else {
          restorePanelSize(panel);
        }
        state._preMiniWasMax = false;
      }
      state.mini = panel.classList.contains('ecap-mini');
      saveState();
    });

    btnMax.addEventListener('click', ()=>{
      // If minimized, un-minimize first (otherwise it looks blank / broken).
      if (panel.classList.contains('ecap-mini')){
        panel.classList.remove('ecap-mini');
        panel.style.height = '';
        panel.style.maxHeight = '';
        state.mini = false;
      }
      panel.classList.toggle('ecap-max');
      state.max = panel.classList.contains('ecap-max');
      if (state.max){
        panel.style.width = '';
        panel.style.height = '';
      } else {
        restorePanelSize(panel);
        clampPanelRect(panel);
      }
      saveState();
    });

    btnPin.addEventListener('click', ()=>{
      state.pinned = !state.pinned;
      panel.classList.toggle('ecap-pinned', !!state.pinned);
      if (state.pinned){
        // reset to default position
        panel.style.left = '';
        panel.style.top = '';
        panel.style.right = '16px';
        panel.style.position = 'fixed';
      }
      saveState();
    });

    btnClose.addEventListener('click', ()=>{ hidePanel(); });

    if (state.closed){
      // Start hidden, but keep the panel fully initialised so it can be reopened via hotkey.
      panel.classList.add('ecap-hidden');
    }

    // drag (disabled when pinned)
    let dragging=false, offX=0, offY=0;
    head.addEventListener('pointerdown', (e)=>{
      if (e.button !== 0) return;
      if (state.pinned) return;
      if (e.target && e.target.closest && e.target.closest('button')) return;
      dragging=true;
      const r = panel.getBoundingClientRect();
      offX = e.clientX - r.left;
      offY = e.clientY - r.top;
      head.setPointerCapture(e.pointerId);
    });
    head.addEventListener('pointermove', (e)=>{
      if (!dragging) return;
      const x = Math.max(8, Math.min(window.innerWidth - panel.offsetWidth - 8, e.clientX - offX));
      const y = Math.max(8, Math.min(window.innerHeight - panel.offsetHeight - 8, e.clientY - offY));
      panel.style.left = x + 'px';
      panel.style.top = y + 'px';
      panel.style.right = 'auto';
    });
    head.addEventListener('pointerup', ()=>{
      if (!dragging) return;
      dragging=false;
      const r = panel.getBoundingClientRect();
      state.left = Math.round(r.left);
      state.top = Math.round(r.top);
      saveState();
    });

    if ('ResizeObserver' in window){
      let resizeSaveTimer = null;
      const resizeObserver = new ResizeObserver(()=>{
        if (panel.classList.contains('ecap-max') || panel.classList.contains('ecap-mini') || panel.classList.contains('ecap-hidden')) return;
        if (resizeSaveTimer) clearTimeout(resizeSaveTimer);
        resizeSaveTimer = setTimeout(()=>{
          try{
            const r = panel.getBoundingClientRect();
            if (r.width >= 360) state.width = Math.round(r.width);
            if (r.height >= 360) state.height = Math.round(r.height);
            clampPanelRect(panel);
            saveState();
          }catch(_){ }
        }, 150);
      });
      try{ resizeObserver.observe(panel); }catch(_){ }
    }
    window.addEventListener('resize', ()=> clampPanelRect(panel));

    // Shared target user
    let targetUser = null;
    function setTargetUser(u, opts){
      targetUser = (u||'').trim() || null;
      const t = document.getElementById('ecapTargetUser');
      if (t) t.textContent = targetUser || '(none)';
      const i = document.getElementById('ecapTargetInput');
      if (i && (opts && opts.syncInput)) i.value = targetUser || '';
      if (opts && opts.loadDetail && targetUser){
        loadUserDetail(targetUser);
      }
    }

    // DASHBOARD
    buildDashSection(secDash);

    const drop = secDash.querySelector('#ecapDrop');
    drop.addEventListener('dragover', (e)=>{ e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', ()=> drop.classList.remove('dragover'));
    drop.addEventListener('drop', (e)=>{
      e.preventDefault(); drop.classList.remove('dragover');
      const u = e.dataTransfer.getData('text/plain') || '';
      if (u) {
        setTargetUser(u, {syncInput:true, loadDetail:true});
        setTab('users');
        toast('ok', 'Target selected', u);
        log(`target set (drop): ${u}`);
      }
    });

    secDash.querySelector('#ecapTargetLoad').addEventListener('click', ()=>{
      const u = (secDash.querySelector('#ecapTargetInput').value || '').trim();
      if (!u) return toast('warn','Missing username','Enter a username first');
      setTargetUser(u, {syncInput:true, loadDetail:true});
      setTab('users');
    });

    async function refreshVoiceSettings(){
      const j = await getJSON('/admin/settings/voice');
      if (j && j.ok){
        const v = String(j.voice_max_room_peers ?? 100);
        const dashInp = secDash.querySelector('#ecapDashVoiceMax');
        if (dashInp) dashInp.value = v;
        const maxInp = secVoice.querySelector('#ecapVoiceMax');
        if (maxInp) maxInp.value = v;
        const enabled = secVoice.querySelector('#ecapVoiceEnabled');
        if (enabled) enabled.checked = !!j.voice_enabled;
        const q = secVoice.querySelector('#ecapVoiceQuality');
        if (q) q.value = j.voice_audio_quality || 'balanced';
        const autoQ = secVoice.querySelector('#ecapVoiceAutoQuality');
        if (autoQ) autoQ.checked = !!j.voice_auto_quality;
        const noise = secVoice.querySelector('#ecapVoiceNoise');
        if (noise) noise.checked = !!j.voice_noise_cancellation;
        const echo = secVoice.querySelector('#ecapVoiceEcho');
        if (echo) echo.checked = !!j.voice_echo_cancellation;
        const gain = secVoice.querySelector('#ecapVoiceGain');
        if (gain) gain.checked = !!j.voice_auto_gain_control;
        const ptt = secVoice.querySelector('#ecapVoicePttDefault');
        if (ptt) ptt.checked = !!j.voice_default_push_to_talk;
        const summary = secVoice.querySelector('#ecapVoiceSummary');
        if (summary) summary.textContent = `Current: ${j.voice_enabled ? 'enabled' : 'disabled'} • ${j.voice_audio_quality || 'balanced'} quality • cap ${j.voice_max_room_peers === 0 ? 'unlimited' : j.voice_max_room_peers} • ${j.voice_noise_cancellation ? 'noise canceling on' : 'noise canceling off'} • ${j.voice_default_push_to_talk ? 'push-to-talk default' : 'hands-free default'}`;
      }
    }

    async function applyVoiceSettings(source){
      const host = source === 'dash' ? secDash : secVoice;
      let v = 0;
      const maxField = source === 'dash' ? secDash.querySelector('#ecapDashVoiceMax') : secVoice.querySelector('#ecapVoiceMax');
      try{ v = parseInt((maxField && maxField.value || '').trim() || '100', 10); }catch(_){ v = 100; }
      if (!isFinite(v) || v < 0) v = 0;
      if (maxField) maxField.value = String(v);
      const payload = { voice_max_room_peers: v };
      if (source !== 'dash'){
        payload.voice_enabled = !!(secVoice.querySelector('#ecapVoiceEnabled') && secVoice.querySelector('#ecapVoiceEnabled').checked);
        payload.voice_audio_quality = (secVoice.querySelector('#ecapVoiceQuality') && secVoice.querySelector('#ecapVoiceQuality').value) || 'balanced';
        payload.voice_auto_quality = !!(secVoice.querySelector('#ecapVoiceAutoQuality') && secVoice.querySelector('#ecapVoiceAutoQuality').checked);
        payload.voice_noise_cancellation = !!(secVoice.querySelector('#ecapVoiceNoise') && secVoice.querySelector('#ecapVoiceNoise').checked);
        payload.voice_echo_cancellation = !!(secVoice.querySelector('#ecapVoiceEcho') && secVoice.querySelector('#ecapVoiceEcho').checked);
        payload.voice_auto_gain_control = !!(secVoice.querySelector('#ecapVoiceGain') && secVoice.querySelector('#ecapVoiceGain').checked);
        payload.voice_default_push_to_talk = !!(secVoice.querySelector('#ecapVoicePttDefault') && secVoice.querySelector('#ecapVoicePttDefault').checked);
      }
      const j = await postJSON('/admin/settings/voice', payload);
      if (j && j.ok){
        log(`voice settings saved: cap=${j.voice_max_room_peers} quality=${j.voice_audio_quality || 'balanced'} auto=${!!j.voice_auto_quality} noise=${!!j.voice_noise_cancellation} ptt=${!!j.voice_default_push_to_talk} kicked=${j.kicked||0}`);
        toast('ok', source === 'dash' ? 'Voice limit updated' : 'Voice settings updated', `cap=${j.voice_max_room_peers === 0 ? 'unlimited' : j.voice_max_room_peers} • kicked=${j.kicked||0}`);
        refreshVoiceSettings();
        refreshStats();
      } else {
        toast('err', 'Voice update failed', j && j.error ? j.error : 'unknown');
      }
    }

    function iceServersToText(list){
      if (!Array.isArray(list) || !list.length) return '';
      try { return JSON.stringify(list, null, 2); } catch { return ''; }
    }

    function renderIceSettings(j){
      if (!j || j.ok === false) return;
      const p2p = secVoice.querySelector('#ecapIceP2p');
      const voice = secVoice.querySelector('#ecapIceVoice');
      const user = secVoice.querySelector('#ecapIceTurnUsername');
      if (p2p && !p2p.matches(':focus')) p2p.value = iceServersToText(j.p2p_ice_servers || []);
      if (voice && !voice.matches(':focus')) voice.value = iceServersToText(j.voice_ice_servers || []);
      if (user && !user.matches(':focus')) {
        const all = [].concat(j.voice_ice_servers || [], j.p2p_ice_servers || []);
        const found = all.find(s => s && s.username);
        user.value = found && found.username ? String(found.username) : '';
      }
      const summary = secVoice.querySelector('#ecapIceSummary');
      if (summary){
        const st = j.summary || {};
        summary.textContent = `ICE: P2P ${st.p2p_count || 0} server(s) • voice/webcam ${st.voice_count || 0} server(s) • STUN ${st.stun_configured ? 'yes' : 'no'} • TURN ${st.turn_configured ? 'yes' : 'no'} • ${st.internet_ready ? 'internet-ready' : 'TURN recommended for real internet tests'}`;
      }
    }

    async function refreshIceSettings(){
      const j = await getJSON('/admin/settings/ice');
      if (j && j.ok) renderIceSettings(j);
      else toast('err', 'ICE status failed', j && j.error ? j.error : 'unknown', 5200);
    }

    async function applyIceSettings(){
      const payload = {
        p2p_ice_servers: (secVoice.querySelector('#ecapIceP2p')?.value || '').trim(),
        voice_ice_servers: (secVoice.querySelector('#ecapIceVoice')?.value || '').trim(),
        turn_username: (secVoice.querySelector('#ecapIceTurnUsername')?.value || '').trim(),
        turn_credential: (secVoice.querySelector('#ecapIceTurnCredential')?.value || '').trim(),
      };
      const j = await postJSON('/admin/settings/ice', payload);
      if (j && j.ok){
        const cred = secVoice.querySelector('#ecapIceTurnCredential');
        if (cred) cred.value = '';
        renderIceSettings(j);
        refreshMediaStatus();
        toast('ok', 'ICE servers saved', j.summary && j.summary.turn_configured ? 'TURN configured' : 'STUN only - add TURN for public internet tests');
      } else {
        toast('err', 'ICE update failed', j && j.error ? j.error : 'unknown', 5200);
      }
    }


    secDash.querySelector('#ecapDashVoiceApply').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'dash:voice-limit', 'Saving', ()=>applyVoiceSettings('dash')));
    const diagBtn = document.getElementById('ecapOpenWebrtcDiag');
    if (diagBtn) diagBtn.addEventListener('click', () => { window.open('/webrtc-diagnostics', '_blank', 'noopener'); });

    // MODERATION
    buildModerationSection(secModeration);

    async function refreshModeration(){
      const j = await getJSON('/admin/moderation/overview');
      const summary = secModeration.querySelector('#ecapModSummary');
      const sanctions = secModeration.querySelector('#ecapModerationSanctions');
      const actions = secModeration.querySelector('#ecapModerationActions');
      const suggestions = secModeration.querySelector('#ecapModerationSuggestions');
      const incidentState = secModeration.querySelector('#ecapIncidentState');
      if (!j || j.ok === false){
        if (summary){ clearNode(summary); summary.appendChild(mutedNode('Moderation data unavailable.', 'span')); }
        if (sanctions) setListStatus(sanctions, 'Unavailable.');
        if (actions) setListStatus(actions, 'Unavailable.');
        return;
      }
      if (summary){
        clearNode(summary);
        const entries = Object.entries(j.summary || {});
        if (!entries.length) summary.appendChild(mutedNode('No active sanction counters.', 'span'));
        entries.forEach(([k,v])=>{
          const pill = el('span', {class:'ecap-pill', text:`${k}: ${v}`});
          summary.appendChild(pill);
        });
      }
      if (incidentState){
        const incident = j.incident || {};
        const mode = incident.mode || 'off';
        incidentState.textContent = `Incident mode: ${mode}${incident.enabled ? ' • active' : ''}`;
        incidentState.className = `ecap-pill ${incident.enabled ? 'warn' : 'ok'}`;
      }
      const renderList = (host, rows, mapper)=>{
        if (!host) return;
        clearNode(host);
        if (!rows || !rows.length){
          host.appendChild(listStatusNode('Nothing recent.'));
          return;
        }
        rows.forEach(row=>host.appendChild(mapper(row)));
      };
      renderList(sanctions, j.active_sanctions || [], (row)=>{
        const item = el('div', {class:'ecap-item'});
        const when = row.created_at ? new Date(row.created_at).toLocaleString() : '—';
        const wrap = el('div');
        wrap.style.minWidth = '0';
        const title = el('div', {text: row.username || 'unknown'});
        title.style.fontWeight = '750';
        const badge = pillNode(row.sanction_type || 'sanction', 'warn');
        badge.style.marginLeft = '6px';
        title.appendChild(document.createTextNode(' '));
        title.appendChild(badge);
        wrap.appendChild(title);
        wrap.appendChild(mutedNode(row.reason || ''));
        wrap.appendChild(mutedNode(when));
        item.appendChild(wrap);
        return item;
      });
      renderList(actions, j.recent_actions || [], (row)=>{
        const item = el('div', {class:'ecap-item'});
        const when = row.timestamp ? new Date(row.timestamp).toLocaleString() : '—';
        const wrap = el('div');
        wrap.style.minWidth = '0';
        const title = el('div', {text: row.action || 'action'});
        title.style.fontWeight = '750';
        wrap.appendChild(title);
        wrap.appendChild(mutedNode(`actor: ${row.actor || '—'} • target: ${row.target || '—'}`));
        wrap.appendChild(mutedNode(when));
        item.appendChild(wrap);
        return item;
      });
      renderList(suggestions, j.suggestions || [], (row)=>{
        const item = el('div', {class:'ecap-item'});
        item.appendChild(mutedNode(row));
        return item;
      });
    }


    function profilePostTextExcerpt(value, maxLen){
      const text = String(value || '').replace(/\n/g, ' ').trim();
      const limit = Math.max(40, Number(maxLen || 260) || 260);
      return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
    }

    function profilePostReasonInput(actionLabel){
      const inp = secModeration.querySelector('#ecapProfilePostReason');
      const reason = String(inp?.value || '').trim();
      if (inp && !reason) {
        inp.value = actionLabel || 'Admin moderation';
      }
      return reason || actionLabel || 'Admin moderation';
    }

    let profilePostModerationSeq = 0;
    let profileReportsSeq = 0;
    let profileBadgesSeq = 0;

    async function refreshProfilePostModeration(){
      const seq = ++profilePostModerationSeq;
      const list = secModeration.querySelector('#ecapProfilePostModerationList');
      if (!list) return;
      const q = (secModeration.querySelector('#ecapProfilePostQuery')?.value || '').trim();
      const status = (secModeration.querySelector('#ecapProfilePostStatus')?.value || 'active').trim();
      const qs = new URLSearchParams({q, status, limit:'80'}).toString();
      clearNode(list);
      list.appendChild(listStatusNode('Loading profile posts…'));
      const j = await getJSON('/admin/profile_posts?' + qs);
      if (seq !== profilePostModerationSeq) return;
      clearNode(list);
      if (!j || j.ok === false){
        list.appendChild(listStatusNode('Profile post moderation unavailable.'));
        return;
      }
      const posts = Array.isArray(j.posts) ? j.posts : [];
      if (!posts.length){
        list.appendChild(listStatusNode('No matching profile posts.'));
        return;
      }
      posts.forEach((post)=>{
        const row = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div');
        title.style.fontWeight = '750';
        title.appendChild(document.createTextNode(`${post.author_username || 'unknown'} · #${post.id}`));
        if (post.deleted_at) {
          const badge = pillNode('deleted', 'warn');
          badge.style.marginLeft = '6px';
          title.appendChild(badge);
        }
        const body = el('div', {class:'ecap-profilePostText', text: profilePostTextExcerpt(post.body || post.link_url || post.gif_url || post.image_url || '(media post)')});
        const created = post.created_at ? new Date(post.created_at).toLocaleString() : '—';
        const meta = el('div', {class:'ecap-profilePostMeta', text:`${post.visibility || 'friends'} • ${Number(post.reaction_count||0)} likes • ${Number(post.comment_count||0)} comments • ${created}`});
        info.append(title, body, meta);

        const actions = el('div', {class:'ecap-actions'});
        const commentsBtn = el('button', {class:'ecap-btn tight', type:'button', text:'Comments'});
        commentsBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-post:${post.id}:comments`, 'Loading', async ()=>{
          await refreshProfilePostComments(row, Number(post.id || 0));
        }));
        actions.appendChild(commentsBtn);
        if (post.deleted_at){
          const restoreBtn = el('button', {class:'ecap-btn tight', type:'button', text:'Restore'});
          restoreBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-post:${post.id}:restore`, 'Restoring', async ()=>{
            const reason = profilePostReasonInput('Restore profile post');
            const r = await postForm('/admin/profile_posts/' + encodeURIComponent(String(post.id)) + '/restore', {reason});
            if (r && r.ok){ toast('ok', 'Post restored', `#${post.id}`); await refreshProfilePostModeration(); }
            else toast('err', 'Restore failed', r && r.error ? r.error : 'unknown');
          }));
          actions.appendChild(restoreBtn);
        } else {
          const deleteBtn = el('button', {class:'ecap-btn danger tight', type:'button', text:'Remove'});
          deleteBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-post:${post.id}:remove`, 'Removing', async ()=>{
            const reason = profilePostReasonInput('Remove profile post');
            const r = await postForm('/admin/profile_posts/' + encodeURIComponent(String(post.id)) + '/delete', {reason});
            if (r && r.ok){ toast('ok', 'Post removed', `#${post.id}`); await refreshProfilePostModeration(); }
            else toast('err', 'Remove failed', r && r.error ? r.error : 'unknown');
          }));
          actions.appendChild(deleteBtn);
        }
        row.append(info, actions);
        list.appendChild(row);
      });
    }

    async function refreshProfilePostComments(row, postId){
      if (!row || !postId) return;
      let box = row.querySelector('.ecap-profileCommentBox');
      if (box){ box.remove(); return; }
      box = el('div', {class:'ecap-profileCommentBox'});
      box.appendChild(listStatusNode('Loading comments…'));
      row.appendChild(box);
      const j = await getJSON('/admin/profile_posts/' + encodeURIComponent(String(postId)) + '/comments?limit=80');
      clearNode(box);
      if (!j || j.ok === false){
        box.appendChild(listStatusNode('Could not load comments.'));
        return;
      }
      const comments = Array.isArray(j.comments) ? j.comments : [];
      if (!comments.length){
        box.appendChild(listStatusNode('No comments.'));
        return;
      }
      comments.forEach((comment)=>{
        const item = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div', {text:`${comment.author_username || 'unknown'} · #${comment.id}`});
        title.style.fontWeight = '750';
        if (comment.deleted_at){
          const badge = pillNode('deleted', 'warn');
          badge.style.marginLeft = '6px';
          title.appendChild(badge);
        }
        info.appendChild(title);
        info.appendChild(el('div', {class:'ecap-profilePostText', text:profilePostTextExcerpt(comment.body || '')}));
        if (comment.deleted_reason) info.appendChild(mutedNode(`reason: ${comment.deleted_reason}`));
        item.appendChild(info);
        if (!comment.deleted_at){
          const del = el('button', {class:'ecap-btn danger tight', type:'button', text:'Remove'});
          del.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-post:${postId}:comment:${comment.id}:remove`, 'Removing', async ()=>{
            const reason = profilePostReasonInput('Remove profile comment');
            const r = await postForm('/admin/profile_posts/' + encodeURIComponent(String(postId)) + '/comments/' + encodeURIComponent(String(comment.id)) + '/delete', {reason});
            if (r && r.ok){ toast('ok', 'Comment removed', `#${comment.id}`); box.remove(); await refreshProfilePostComments(row, postId); }
            else toast('err', 'Comment remove failed', r && r.error ? r.error : 'unknown');
          }));
          item.appendChild(del);
        }
        box.appendChild(item);
      });
    }


    function profileReportReasonInput(actionLabel){
      const inp = secModeration.querySelector('#ecapProfileReportReason');
      const reason = String(inp?.value || '').trim();
      if (inp && !reason) inp.value = actionLabel || 'Admin profile report review';
      return reason || actionLabel || 'Admin profile report review';
    }

    async function refreshProfileReports(){
      const seq = ++profileReportsSeq;
      const list = secModeration.querySelector('#ecapProfileReportsList');
      if (!list) return;
      const q = (secModeration.querySelector('#ecapProfileReportQuery')?.value || '').trim();
      const status = (secModeration.querySelector('#ecapProfileReportStatus')?.value || 'open').trim();
      const qs = new URLSearchParams({q, status, limit:'80'}).toString();
      clearNode(list);
      list.appendChild(listStatusNode('Loading profile reports…'));
      const j = await getJSON('/admin/profile_reports?' + qs);
      if (seq !== profileReportsSeq) return;
      clearNode(list);
      if (!j || j.ok === false){
        list.appendChild(listStatusNode('Profile reports queue unavailable.'));
        return;
      }
      const reports = Array.isArray(j.reports) ? j.reports : [];
      if (!reports.length){
        list.appendChild(listStatusNode('No matching profile reports.'));
        return;
      }
      reports.forEach((report)=>{
        const row = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div');
        title.style.fontWeight = '750';
        title.appendChild(document.createTextNode(`#${report.id} ${report.reporter_username || 'unknown'} → ${report.target_username || 'unknown'}`));
        const statusPill = pillNode(report.status || 'open', report.status === 'open' ? 'warn' : '');
        statusPill.style.marginLeft = '6px';
        title.appendChild(statusPill);
        const targetText = report.comment_id ? `comment #${report.comment_id} on post #${report.post_id}` : `post #${report.post_id}`;
        const created = report.created_at ? new Date(report.created_at).toLocaleString() : '—';
        const body = report.comment_body || report.post_body || report.post_link_url || report.post_gif_url || report.post_image_url || '(media post)';
        info.appendChild(title);
        info.appendChild(el('div', {class:'ecap-profilePostMeta', text:`${targetText} • ${report.reason || 'other'} • ${created}`}));
        if (report.details) info.appendChild(el('div', {class:'ecap-profilePostText', text:profilePostTextExcerpt(report.details, 220)}));
        info.appendChild(el('div', {class:'ecap-profilePostText', text:profilePostTextExcerpt(body, 260)}));
        if (report.reviewed_by) info.appendChild(mutedNode(`reviewed by ${report.reviewed_by}: ${report.action_taken || report.status || 'reviewed'}`));
        const actions = el('div', {class:'ecap-actions'});
        if (String(report.status || 'open') === 'open'){
          const dismiss = el('button', {class:'ecap-btn tight', type:'button', text:'Dismiss'});
          dismiss.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-report:${report.id}:dismiss`, 'Dismissing', async ()=>{
            const reason = profileReportReasonInput('Dismiss profile report');
            const r = await postForm('/admin/profile_reports/' + encodeURIComponent(String(report.id)) + '/dismiss', {reason});
            if (r && r.ok){ toast('ok', 'Report dismissed', `#${report.id}`); await refreshProfileReports(); }
            else toast('err', 'Dismiss failed', r && r.error ? r.error : 'unknown');
          }));
          const warn = el('button', {class:'ecap-btn warn tight', type:'button', text:'Warn user'});
          warn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-report:${report.id}:warn`, 'Warning', async ()=>{
            const reason = profileReportReasonInput('Your profile content was reported and reviewed by an admin. Please follow the chat rules.');
            const r = await postForm('/admin/profile_reports/' + encodeURIComponent(String(report.id)) + '/warn', {reason});
            if (r && r.ok){ toast('ok', 'Warning sent', report.target_username || 'user'); await refreshProfileReports(); }
            else toast('err', 'Warn failed', r && r.error ? r.error : 'unknown');
          }));
          const remove = el('button', {class:'ecap-btn danger tight', type:'button', text:'Remove content'});
          remove.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-report:${report.id}:remove-content`, 'Removing', async ()=>{
            const reason = profileReportReasonInput('Remove reported profile content');
            const r = await postForm('/admin/profile_reports/' + encodeURIComponent(String(report.id)) + '/delete_content', {reason});
            if (r && r.ok){ toast('ok', 'Reported content removed', `#${report.id}`); await refreshProfileReports(); await refreshProfilePostModeration(); }
            else toast('err', 'Remove failed', r && r.error ? r.error : 'unknown');
          }));
          actions.append(dismiss, warn, remove);
        }
        row.append(info, actions);
        list.appendChild(row);
      });
    }

    function currentBadgeUsername(){ return String(secModeration.querySelector('#ecapProfileBadgeUser')?.value || '').trim(); }

    async function refreshProfileBadges(){
      const seq = ++profileBadgesSeq;
      const list = secModeration.querySelector('#ecapProfileBadgeList');
      if (!list) return;
      const username = currentBadgeUsername();
      clearNode(list);
      if (!username){ list.appendChild(listStatusNode('Enter a username to load badges.')); return; }
      list.appendChild(listStatusNode('Loading badges…'));
      const j = await getJSON('/admin/profile_badges/' + encodeURIComponent(username));
      if (seq !== profileBadgesSeq || username !== currentBadgeUsername()) return;
      clearNode(list);
      if (!j || j.ok === false){
        list.appendChild(listStatusNode('Profile badge tools unavailable.'));
        return;
      }
      const badges = Array.isArray(j.badges) ? j.badges : [];
      if (!badges.length){ list.appendChild(listStatusNode('No assigned badges.')); return; }
      badges.forEach((badge)=>{
        const row = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        info.appendChild(el('div', {style:'font-weight:750', text:`${badge.label || badge.badge_key || 'Badge'} · ${badge.badge_key || ''}`}));
        info.appendChild(mutedNode(`assigned by ${badge.assigned_by || 'admin'}${badge.reason ? ` • ${badge.reason}` : ''}`));
        const del = el('button', {class:'ecap-btn danger tight', type:'button', text:'Remove'});
        del.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `profile-badge:${username}:${badge.badge_key}:remove`, 'Removing', async ()=>{
          const r = await postForm('/admin/profile_badges/' + encodeURIComponent(username) + '/' + encodeURIComponent(String(badge.badge_key || '')) + '/delete', {reason:String(secModeration.querySelector('#ecapProfileBadgeReason')?.value || '').trim()});
          if (r && r.ok){ toast('ok', 'Badge removed', badge.badge_key || 'badge'); await refreshProfileBadges(); }
          else toast('err', 'Badge remove failed', r && r.error ? r.error : 'unknown');
        }));
        row.append(info, del);
        list.appendChild(row);
      });
    }

    async function assignProfileBadge(){
      const username = currentBadgeUsername();
      const badge_key = String(secModeration.querySelector('#ecapProfileBadgeKey')?.value || '').trim();
      const label = String(secModeration.querySelector('#ecapProfileBadgeLabel')?.value || '').trim();
      const reason = String(secModeration.querySelector('#ecapProfileBadgeReason')?.value || '').trim();
      if (!username || !badge_key || !label){ toast('err', 'Badge missing info', 'username, badge key, and label are required'); return; }
      const r = await postForm('/admin/profile_badges/' + encodeURIComponent(username), {badge_key, label, reason});
      if (r && r.ok){ toast('ok', 'Badge assigned', label); await refreshProfileBadges(); }
      else toast('err', 'Badge assign failed', r && r.error ? r.error : 'unknown');
    }

    secModeration.querySelector('#ecapModerationRefresh').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'moderation:refresh', 'Loading', refreshModeration));
    secModeration.querySelector('#ecapOpenSafety').addEventListener('click', ()=>setTab('safety'));
    secModeration.querySelector('#ecapProfilePostRefresh')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'profile-posts:refresh', 'Loading', refreshProfilePostModeration));
    secModeration.querySelector('#ecapProfilePostQuery')?.addEventListener('input', debounce(refreshProfilePostModeration, 320));
    secModeration.querySelector('#ecapProfilePostStatus')?.addEventListener('change', refreshProfilePostModeration);
    secModeration.querySelector('#ecapProfileReportRefresh')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'profile-reports:refresh', 'Loading', refreshProfileReports));
    secModeration.querySelector('#ecapProfileReportQuery')?.addEventListener('input', debounce(refreshProfileReports, 320));
    secModeration.querySelector('#ecapProfileReportStatus')?.addEventListener('change', refreshProfileReports);
    secModeration.querySelector('#ecapProfileBadgeLoad')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'profile-badges:load', 'Loading', refreshProfileBadges));
    secModeration.querySelector('#ecapProfileBadgeAssign')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'profile-badges:assign', 'Saving', assignProfileBadge));
    secModeration.querySelector('#ecapProfileBadgeUser')?.addEventListener('keydown', (ev)=>{ if (ev.key === 'Enter') refreshProfileBadges(); });
    refreshModeration();
    refreshProfilePostModeration();
    refreshProfileReports();
    refreshProfileBadges();

    // USERS
    buildUsersSection(secUsers);

    const qInp = secUsers.querySelector('#ecapUserQuery');
    const qMode = secUsers.querySelector('#ecapUserMode');
    const qOnline = secUsers.querySelector('#ecapUserOnlineOnly');
    const qAdmins = secUsers.querySelector('#ecapUserAdminsOnly');
    const qStatus = secUsers.querySelector('#ecapUserStatus');
    const qLimit = secUsers.querySelector('#ecapUserLimit');
    const userPrevPage = secUsers.querySelector('#ecapUserPrevPage');
    const userNextPage = secUsers.querySelector('#ecapUserNextPage');
    const userPageInfo = secUsers.querySelector('#ecapUserPageInfo');
    const resBox = secUsers.querySelector('#ecapUserResults');
    const selInp = secUsers.querySelector('#ecapSelUser');
    const summaryBox = secUsers.querySelector('#ecapUserSummary');
    const userTimelineBox = secUsers.querySelector('#ecapUserTimeline');
    const userTimelineMeta = secUsers.querySelector('#ecapUserTimelineMeta');
    const userTimelineDays = secUsers.querySelector('#ecapUserTimelineDays');
    const userTimelineRefresh = secUsers.querySelector('#ecapUserTimelineRefresh');

    const actSession = secUsers.querySelector('#ecapActSession');
    const actAccount = secUsers.querySelector('#ecapActAccount');
    const actSecurity = secUsers.querySelector('#ecapActSecurity');
    const actMod = secUsers.querySelector('#ecapActMod');

    const userSearchState = { page: 1, hasMore: false, lastReturned: 0, loading: false };
    let userSearchSeq = 0;
    let userDetailSeq = 0;
    let userTimelineSeq = 0;

    function updateUserPager(meta){
      const m = meta || {};
      userSearchState.hasMore = !!m.has_more;
      userSearchState.lastReturned = Number(m.returned || (Array.isArray(m.users) ? m.users.length : 0) || 0);
      const page = Number(m.page || userSearchState.page || 1);
      userSearchState.page = Math.max(1, page);
      if (userPrevPage) userPrevPage.disabled = userSearchState.loading || userSearchState.page <= 1;
      if (userNextPage) userNextPage.disabled = userSearchState.loading || !userSearchState.hasMore;
      if (userPageInfo){
        if (m.requires_query){
          userPageInfo.textContent = 'Search or enable a filter before loading users';
        } else if (userSearchState.loading){
          userPageInfo.textContent = 'Loading users…';
        } else {
          const more = userSearchState.hasMore ? ' • more available' : ' • end';
          userPageInfo.textContent = `Page ${userSearchState.page} • ${userSearchState.lastReturned} shown${more}`;
        }
      }
    }

    // Create user controls (admin)
    const cuUser = secUsers.querySelector('#ecapCreateUser');
    const cuEmail = secUsers.querySelector('#ecapCreateEmail');
    const cuPass = secUsers.querySelector('#ecapCreatePass');
    const cuPin = secUsers.querySelector('#ecapCreatePin');
    const cuIsAdmin = secUsers.querySelector('#ecapCreateIsAdmin');
    const cuBtn = secUsers.querySelector('#ecapCreateBtn');
    const cuPassMeter = secUsers.querySelector('#ecapCreatePassMeter');
    const cuUserAvailability = secUsers.querySelector('#ecapCreateUserAvailability');
    let cuUsernameStatus = 'idle';
    let cuUsernameCheckSeq = 0;

    function setCreateUsernameAvailability(status, message){
      cuUsernameStatus = status || 'idle';
      if (!cuUserAvailability) return;
      cuUserAvailability.dataset.usernameState = cuUsernameStatus;
      cuUserAvailability.classList.toggle('available', cuUsernameStatus === 'available');
      cuUserAvailability.classList.toggle('taken', cuUsernameStatus === 'taken');
      cuUserAvailability.classList.toggle('invalid', cuUsernameStatus === 'invalid');
      cuUserAvailability.classList.toggle('checking', cuUsernameStatus === 'checking');
      cuUserAvailability.classList.toggle('unknown', cuUsernameStatus === 'unknown');
      cuUserAvailability.textContent = message || '';
    }

    async function checkCreateUsernameAvailability(){
      if (!cuUser) return;
      const username = (cuUser.value || '').trim();
      cuUsernameCheckSeq += 1;
      const seq = cuUsernameCheckSeq;
      if (!username){
        setCreateUsernameAvailability('idle', 'Enter a username to check availability.');
        return;
      }
      if (username.length < ECAP_USERNAME_MIN){
        setCreateUsernameAvailability('invalid', `Username too short. Use at least ${ECAP_USERNAME_MIN} characters.`);
        return;
      }
      if (username.length > ECAP_USERNAME_MAX){
        setCreateUsernameAvailability('invalid', `Username too long. Use ${ECAP_USERNAME_MAX} characters or fewer.`);
        return;
      }
      if (cuUser.validity && cuUser.validity.patternMismatch){
        setCreateUsernameAvailability('invalid', ECAP_USERNAME_TITLE || 'Username not allowed.');
        return;
      }
      setCreateUsernameAvailability('checking', 'Checking username…');
      try{
        const r = await adminFetch('/api/username_available?username=' + encodeURIComponent(username), {method:'GET', headers:{'Accept':'application/json'}});
        const j = await r.json().catch(()=>null);
        if (seq !== cuUsernameCheckSeq) return;
        const status = j && j.status ? String(j.status) : (r.ok ? 'unknown' : 'unknown');
        const msg = j && j.message ? String(j.message) : (r.ok ? 'Could not read username check.' : 'Could not check username right now.');
        if (status === 'available' && j && j.available === true) setCreateUsernameAvailability('available', msg || 'Username is available.');
        else if (status === 'taken') setCreateUsernameAvailability('taken', msg || 'Username already exists.');
        else if (status === 'invalid') setCreateUsernameAvailability('invalid', msg || 'Username not allowed.');
        else setCreateUsernameAvailability('unknown', msg || 'Username availability could not be checked.');
      }catch(_){
        if (seq !== cuUsernameCheckSeq) return;
        setCreateUsernameAvailability('unknown', 'Could not check username right now. The server will verify it on submit.');
      }
    }

    const debouncedCreateUsernameCheck = debounce(checkCreateUsernameAvailability, 300);
    if (cuUser){
      cuUser.addEventListener('input', debouncedCreateUsernameCheck);
      cuUser.addEventListener('change', checkCreateUsernameAvailability);
    }
    checkCreateUsernameAvailability();

    function compactPassValue(v){ return String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, ''); }
    function deobfuscatePassValue(v){ return compactPassValue(v).replace(/[013457@$!]/g, ch => ({'0':'o','1':'l','3':'e','4':'a','5':'s','7':'t','@':'a','$':'s','!':'i'}[ch] || ch)); }
    function hasRepeatedPassChunk(compacted){
      if (!compacted || compacted.length < ECAP_PASSWORD_MIN) return false;
      const limit = Math.min(8, Math.max(1, Math.floor(compacted.length / 2)));
      for (let size = 1; size <= limit; size += 1){
        if (compacted.length % size !== 0) continue;
        const repeats = compacted.length / size;
        if (repeats < 3) continue;
        const chunk = compacted.slice(0, size);
        if (chunk && chunk.repeat(repeats) === compacted) return true;
      }
      return false;
    }
    function hasObviousPassSequence(compacted){
      if (!compacted || compacted.length < ECAP_PASSWORD_MIN) return false;
      const sequences = ['abcdefghijklmnopqrstuvwxyz','zyxwvutsrqponmlkjihgfedcba','qwertyuiop','poiuytrewq','asdfghjkl','lkjhgfdsa','zxcvbnm','mnbvcxz','1234567890','0987654321'];
      return sequences.some(seq => {
        for (let size = 6; size <= seq.length; size += 1){
          for (let start = 0; start <= seq.length - size; start += 1){
            if (compacted.includes(seq.slice(start, start + size))) return true;
          }
        }
        return false;
      });
    }
    function isDigitPaddedWeakPassSeed(compacted){
      const seeds = ['administrator','defaultpassword','mikeschatserver','changeme','mikeserver','echochat','iloveyou','letmein','welcome','qwerty','admin'];
      return seeds.some(seed => {
        if (compacted.startsWith(seed)){
          const rest = compacted.slice(seed.length);
          if (rest && /^[0-9]+$/.test(rest)) return true;
        }
        if (compacted.endsWith(seed)){
          const rest = compacted.slice(0, compacted.length - seed.length);
          if (rest && /^[0-9]+$/.test(rest)) return true;
        }
        return false;
      });
    }
    function isSeededWeakPass(password, compacted, common){
      const folded = String(password || '').toLowerCase();
      const variants = [compacted, deobfuscatePassValue(password)];
      return variants.some(variant => variant && (common.has(variant) || isDigitPaddedWeakPassSeed(variant) || hasObviousPassSequence(variant))) || folded.includes('passw0rd');
    }
    function updateCreatePasswordMeter(){
      if (!cuPassMeter || !cuPass) return;
      const password = cuPass.value || '';
      const compacted = compactPassValue(password);
      const username = compactPassValue(cuUser?.value || '');
      const emailLocal = compactPassValue(String(cuEmail?.value || '').split('@', 1)[0] || '');
      const serverName = compactPassValue(SERVER_NAME);
      const terms = [username, emailLocal, serverName].filter(t => t.length >= 4);
      const common = ECAP_PASSWORD_COMMON_WEAK;
      const lengthOk = password.length >= ECAP_PASSWORD_MIN;
      const recommendedOk = password.length >= ECAP_PASSWORD_RECOMMENDED;
      const contextOk = !terms.some(t => compacted.includes(t));
      const commonOk = !common.has(compacted) && !String(password || '').toLowerCase().includes('password') && !isSeededWeakPass(password, compacted, common) && !hasObviousPassSequence(compacted);
      const hasControlChars = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(password);
      const tooLong = password.length > ECAP_PASSWORD_MAX;
      const charsOk = !tooLong && !hasControlChars;
      const variety = [[/[a-z]/, password], [/[A-Z]/, password], [/[0-9]/, password], [/[^A-Za-z0-9\s]/, password], [/\s/, password]].reduce((n, pair)=> n + (pair[0].test(pair[1]) ? 1 : 0), 0);
      let score = 0;
      if (lengthOk) score += 1;
      if (recommendedOk) score += 1;
      if (password.length >= 28) score += 1;
      if (/\s/.test(password) || /[A-Za-z]{4,}/.test(password)) score += 1;
      if (variety >= 2) score += 1;
      if (variety >= 3) score += 1;
      const repetitiveOk = !(compacted.length >= ECAP_PASSWORD_MIN && (new Set(compacted.split('')).size <= 2 || hasRepeatedPassChunk(compacted)));
      if (!contextOk || !commonOk || !repetitiveOk || !charsOk) score = Math.min(score, 1);
      score = Math.max(0, Math.min(5, score));
      const label = !password ? 'Start typing' : (!lengthOk ? 'Too short' : (tooLong ? 'Too long' : (hasControlChars ? 'Remove hidden characters' : (!contextOk ? 'Contains account details' : ((!commonOk || !repetitiveOk) ? 'Too common/repetitive' : (score >= 5 ? 'Excellent' : score >= 4 ? 'Strong' : score >= 3 ? 'Good' : 'Usable'))))));
      const fill = cuPassMeter.querySelector('.ecap-passMeterFill');
      const labelNode = cuPassMeter.querySelector('.ecap-passMeterLabel');
      if (fill) fill.style.width = password ? Math.max(8, Math.round((score / 5) * 100)) + '%' : '0';
      if (labelNode) labelNode.textContent = label;
      const states = {length: lengthOk, recommended: recommendedOk, context: contextOk, common: commonOk && repetitiveOk, chars: charsOk};
      Object.entries(states).forEach(([key, ok])=>{
        const row = cuPassMeter.querySelector(`[data-pass-rule="${key}"]`);
        if (row){
          row.classList.toggle('pass', !!ok);
          row.textContent = (ok ? '✓ ' : '• ') + row.textContent.replace(/^[✓•]\s*/, '');
        }
      });
    }
    [cuPass, cuUser, cuEmail].forEach(elm => { if (elm) elm.addEventListener('input', updateCreatePasswordMeter); });
    updateCreatePasswordMeter();

    if (cuBtn) cuBtn.addEventListener('click', (ev)=> withAdminAction(cuBtn, 'create-user', 'Creating', async ()=>{
      const username = (cuUser?.value||'').trim();
      const password = cuPass?.value || '';
      const email = (cuEmail?.value||'').trim();
      const recovery_pin = (cuPin?.value||'').trim();
      const is_admin = cuIsAdmin?.checked ? '1' : '0';
      if (!username || !password || !email || !recovery_pin) return toast('warn','Missing fields','Username, email, password, and Recovery PIN required');
      if (cuUsernameStatus === 'taken') return toast('warn','Username taken','Choose a different username before creating the account.');
      if (cuUsernameStatus === 'invalid') return toast('warn','Invalid username', cuUserAvailability ? cuUserAvailability.textContent : 'Username is not allowed.');
      if (password.length < ECAP_PASSWORD_MIN) return toast('warn','Password too short',`Use at least ${ECAP_PASSWORD_MIN} characters. A longer passphrase is better than forced symbols.`);
      if (!/^\d{4,8}$/.test(recovery_pin)) return toast('warn','Invalid Recovery PIN','PIN must be 4 to 8 digits');
      const j = await postForm('/admin/create_user', {username, password, email, recovery_pin, is_admin});
      if (j && (j.ok || j.status === 'created')){
        log(`created user ${username} (admin=${is_admin})`);
        toast('ok','User created', username);
        cuPass.value = '';
        if (cuPin) cuPin.value = '';
        if (qInp && !qInp.value.trim()) qInp.value = username;
        runSearch({resetPage:true});
      } else {
        toast('err','Create user failed', j && j.error ? j.error : 'unknown');
      }
    }));

    function appendUserBadges(host, u){
      if (!host) return;
      const isAdmin = !!(u && (u.effective_is_admin || u.is_admin));
      if (u.online) host.appendChild(pillNode('online', 'ok'));
      if (isAdmin) host.appendChild(pillNode('admin', 'warn'));
      if (u.status && u.status !== 'active') host.appendChild(pillNode(u.status, 'bad'));
    }

    function renderSearchResults(payload){
      const users = Array.isArray(payload?.users) ? payload.users : [];
      clearNode(resBox);
      updateUserPager(payload || {});
      if (payload && payload.requires_query){
        resBox.appendChild(listStatusNode(payload.message || 'Search by username, email, or ID before loading users.'));
        return;
      }
      if (!users.length){
        resBox.appendChild(listStatusNode('No results on this page'));
        return;
      }
      for (const u of users){
        const row = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const name = el('div', {text: u.username || ''});
        name.style.fontWeight = '780';
        name.style.whiteSpace = 'nowrap';
        name.style.overflow = 'hidden';
        name.style.textOverflow = 'ellipsis';
        const email = mutedNode(u.email || '');
        email.style.whiteSpace = 'nowrap';
        email.style.overflow = 'hidden';
        email.style.textOverflow = 'ellipsis';
        info.appendChild(name);
        info.appendChild(email);
        const controls = el('div');
        controls.style.display = 'flex';
        controls.style.gap = '8px';
        controls.style.alignItems = 'center';
        controls.style.flex = '0 0 auto';
        appendUserBadges(controls, u);
        const btn = el('button', {class:'ecap-btn tight', type:'button', text:'Load'});
        controls.appendChild(btn);
        row.appendChild(info);
        row.appendChild(controls);
        btn.addEventListener('click', (e)=>{
          e.stopPropagation();
          setTargetUser(u.username, {syncInput:true, loadDetail:true});
          toast('info','Loaded', u.username);
        });
        row.addEventListener('click', ()=>{
          setTargetUser(u.username, {syncInput:true, loadDetail:true});
        });
        resBox.appendChild(row);
      }
    }

    async function runSearch(opts){
      const options = opts || {};
      if (options.resetPage) userSearchState.page = 1;
      const q = (qInp.value||'').trim();
      const mode = qMode.value || 'contains';
      const online = qOnline.checked ? '1':'0';
      const admins = qAdmins.checked ? '1':'0';
      const status = qStatus.value || 'any';
      const limit = (qLimit && qLimit.value) ? qLimit.value : '50';
      const page = String(Math.max(1, userSearchState.page || 1));
      const qs = new URLSearchParams({q, mode, online, admins, status, limit, page}).toString();
      const seq = ++userSearchSeq;
      userSearchState.loading = true;
      updateUserPager({page:userSearchState.page, returned:0, has_more:false});
      let j = await getJSON('/admin/user_search?'+qs);
      if (seq !== userSearchSeq) return;

      // beta.396: keep the admin Users panel usable on upgraded databases if
      // the enhanced search endpoint hits schema drift. The backend is now
      // schema-tolerant too, but this UI fallback gives admins a working
      // username search even if an old server process or stale route is still loaded.
      if (!j || !Array.isArray(j.users)) {
        const legacyQs = new URLSearchParams({
          prefix: q,
          limit,
          browse: q ? '0' : '1'
        }).toString();
        const legacy = await getJSON('/admin/users?' + legacyQs);
        if (seq !== userSearchSeq) return;
        if (legacy && Array.isArray(legacy.users)) {
          j = {
            ok: true,
            users: legacy.users,
            q,
            mode: 'prefix',
            limit: Number(limit) || 50,
            page: userSearchState.page || 1,
            returned: legacy.users.length,
            has_more: !!legacy.has_more,
            next_page: legacy.has_more ? ((userSearchState.page || 1) + 1) : null,
            requires_query: false,
            fallback: 'legacy_admin_users'
          };
          log(`admin user search fallback used for "${q || '*'}"`);
        }
      }

      userSearchState.loading = false;
      if (j && Array.isArray(j.users)) renderSearchResults(j);
      else {
        updateUserPager({page:userSearchState.page, returned:0, has_more:false});
        const detail = j && (j.error || j.message) ? ` (${j.error || j.message})` : '';
        setListStatus(resBox, 'User search unavailable' + detail + '.');
      }
    }

    const runSearchReset = ()=>runSearch({resetPage:true});
    const runSearchDebounced = debounce(runSearchReset, 220);
    qInp.addEventListener('input', runSearchDebounced);
    qMode.addEventListener('change', runSearchReset);
    qOnline.addEventListener('change', runSearchReset);
    qAdmins.addEventListener('change', runSearchReset);
    qStatus.addEventListener('change', runSearchReset);
    if (qLimit) qLimit.addEventListener('change', runSearchReset);
    if (userPrevPage) userPrevPage.addEventListener('click', ()=>{
      if (userSearchState.page <= 1) return;
      userSearchState.page -= 1;
      runSearch();
    });
    if (userNextPage) userNextPage.addEventListener('click', ()=>{
      if (!userSearchState.hasMore) return;
      userSearchState.page += 1;
      runSearch();
    });
    secUsers.querySelector('#ecapUserSearchBtn').addEventListener('click', runSearchReset);
    qInp.addEventListener('keydown', (e)=>{
      if (e.key === 'Enter'){ e.preventDefault(); runSearchReset(); }
    });
    renderSearchResults({users:[], requires_query:true, page:1, returned:0, has_more:false, message:'Search by username/email/id or enable a filter. The full user table is never loaded into this panel.'});

    secUsers.querySelector('#ecapLoadUser').addEventListener('click', ()=>{
      const u = (selInp.value||'').trim();
      if (!u) return toast('warn','Missing username','Enter a username');
      setTargetUser(u, {syncInput:true, loadDetail:true});
    });
    userTimelineRefresh?.addEventListener('click', ()=>refreshUserActivityTimeline(selInp.value));
    userTimelineDays?.addEventListener('change', ()=>refreshUserActivityTimeline(selInp.value));

    async function loadUserDetail(u){
      if (!u) return;
      const requested = String(u || '').trim();
      const seq = ++userDetailSeq;
      selInp.value = requested;
      clearNode(summaryBox);
      summaryBox.appendChild(mutedNode(`Loading ${requested}…`));
      [actSession,actAccount,actSecurity,actMod].forEach(clearNode);

      const j = await getJSON('/admin/user_detail/' + encodeURIComponent(requested));
      if (seq !== userDetailSeq || String(selInp.value || '').trim() !== requested) return;
      if (!j || !j.user){
        clearNode(summaryBox);
        summaryBox.appendChild(mutedNode('Not found.'));
        [actSession,actAccount,actSecurity,actMod].forEach(clearNode);
        return;
      }
      renderUserDetail(j);
      refreshUserActivityTimeline(requested);
    }

    function timelineCategoryClass(category){
      const key = String(category || '').toLowerCase();
      if (key.includes('moderation') || key.includes('report')) return 'warn';
      if (key.includes('live') || key.includes('session')) return 'ok';
      if (key.includes('message')) return '';
      if (key.includes('profile')) return '';
      return '';
    }

    function renderUserActivityTimeline(payload){
      clearNode(userTimelineBox);
      const events = Array.isArray(payload?.events) ? payload.events : [];
      if (userTimelineMeta){
        userTimelineMeta.textContent = payload?.username ? `${events.length} event${events.length === 1 ? '' : 's'} • last ${payload.days || '?'} days` : 'Load a user to view activity.';
      }
      if (!events.length){
        userTimelineBox.appendChild(listStatusNode('No activity found for this window.'));
        return;
      }
      events.forEach((event)=>{
        const row = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div');
        title.style.fontWeight = '750';
        title.appendChild(document.createTextNode(event.summary || event.action || 'activity'));
        const cat = pillNode(event.category || 'activity', timelineCategoryClass(event.category));
        cat.style.marginLeft = '6px';
        title.appendChild(cat);
        const when = event.timestamp ? new Date(event.timestamp).toLocaleString() : '—';
        const meta = `${when}${event.source ? ` • ${event.source}` : ''}${event.action ? ` • ${event.action}` : ''}`;
        info.appendChild(title);
        info.appendChild(mutedNode(meta));
        if (event.details) info.appendChild(el('div', {class:'ecap-profilePostText', text:String(event.details || '').slice(0, 300)}));
        row.appendChild(info);
        userTimelineBox.appendChild(row);
      });
    }

    async function refreshUserActivityTimeline(username){
      const target = String(username || selInp?.value || '').trim();
      const seq = ++userTimelineSeq;
      if (!target){
        clearNode(userTimelineBox);
        userTimelineBox.appendChild(listStatusNode('Load a user to view activity.'));
        if (userTimelineMeta) userTimelineMeta.textContent = 'Load a user to view activity.';
        return;
      }
      const days = String(userTimelineDays?.value || '30');
      clearNode(userTimelineBox);
      userTimelineBox.appendChild(listStatusNode('Loading activity timeline…'));
      if (userTimelineMeta) userTimelineMeta.textContent = `Loading ${target}…`;
      const qs = new URLSearchParams({days, limit:'100'}).toString();
      const j = await getJSON('/admin/users/' + encodeURIComponent(target) + '/activity_timeline?' + qs);
      if (seq !== userTimelineSeq || String(selInp?.value || '').trim() !== target) return;
      if (!j || j.ok === false){
        clearNode(userTimelineBox);
        userTimelineBox.appendChild(listStatusNode('Activity timeline unavailable.'));
        if (userTimelineMeta) userTimelineMeta.textContent = j && j.error ? j.error : 'Timeline unavailable.';
        return;
      }
      renderUserActivityTimeline(j);
    }

    function kv(label, value){
      const d = el('div', {class:'ecap-stat'});
      d.appendChild(el('div', {class:'lbl', text:label || ''}));
      const val = el('div', {class:'val', text:value || '—'});
      val.style.fontSize = '13px';
      d.appendChild(val);
      return d;
    }

    function renderUserDetail(payload){
      const u = payload.user || {};
      const roles = (payload.roles || []).join(', ') || '—';
      const sanctions = payload.sanctions || [];
      const quota = payload.quota ? `${payload.quota.messages_per_hour}/hr` : '—';
      const lastSeen = u.last_seen ? new Date(u.last_seen).toLocaleString() : '—';
      const created = u.created_at ? new Date(u.created_at).toLocaleString() : '—';
      const counts = payload.counts || {};

      clearNode(summaryBox);
      summaryBox.appendChild(kv('Email', u.email || '—'));
      summaryBox.appendChild(kv('Status', u.status || '—'));
      summaryBox.appendChild(kv('Online', u.online ? 'yes' : 'no'));
      summaryBox.appendChild(kv('Last seen', lastSeen));
      summaryBox.appendChild(kv('Created', created));
      summaryBox.appendChild(kv('2FA', u.two_factor_enabled ? 'enabled' : 'off'));
      summaryBox.appendChild(kv('Admin access', (u.effective_is_admin || u.is_admin) ? 'yes' : 'no'));
      summaryBox.appendChild(kv('Roles', roles));
      summaryBox.appendChild(kv('Quota', quota));
      summaryBox.appendChild(kv('Friends', String(counts.friends ?? '—')));
      summaryBox.appendChild(kv('Groups', String(counts.groups ?? '—')));
      summaryBox.appendChild(kv('Sanctions', String(sanctions.length)));
      summaryBox.appendChild(kv('Live sessions', String(payload.connected_session_count ?? 0)));

      [actSession,actAccount,actSecurity,actMod].forEach(clearNode);

      function addAction(group, label, fn, css){
        const b = el('button', {class:`ecap-btn tight ${css||''}`, text:label, type:'button'});
        b.addEventListener('click', (ev)=>{
          ev.preventDefault();
          ev.stopPropagation();
          withAdminAction(b, `user:${username}:${label}`, `${label}…`, ()=>fn(ev));
        });
        group.appendChild(b);
      }

      const username = u.username;

      addAction(actSession, 'Force logout', async ()=>{
        const reason = await adminPrompt('Force logout', `Reason shown to ${username}.`, {label:'Reason', value:'Logged out by an admin', maxlength:180}, {confirmText:'Force logout'});
        if (reason === null) return;
        const j = await postForm('/admin/force_logout/' + encodeURIComponent(username), {reason});
        if (j && j.ok){
          const sessions = Number(j.revoked_sessions || 0);
          const tokens = Number(j.revoked_tokens || 0);
          log(`force logged out ${username}; sessions=${sessions}; tokens=${tokens}; disconnected=${j.disconnected_sessions || 0}`);
          toast('ok','User logged out', `${username}: ${sessions} session(s), ${tokens} token(s) revoked`);
          loadUserDetail(username);
        }
        else toast('err','Force logout failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actAccount, 'Deactivate', async ()=>{
        const ok = await adminConfirm('Deactivate user', `Deactivate ${username}?`, {danger:true, confirmText:'Deactivate'});
        if (!ok) return;
        const j = await postForm('/admin/deactivate_user/' + encodeURIComponent(username), {});
        if (j && j.ok){ log(`deactivated ${username}`); toast('ok','Deactivated', username); loadUserDetail(username); }
        else toast('err','Deactivate failed', j && j.error ? j.error : 'unknown');
      }, 'danger');

      addAction(actAccount, 'Delete', async ()=>{
        const ok = await adminConfirm('Delete user', `DELETE ${username}? This cannot be undone.`, {danger:true, confirmText:'Delete'});
        if (!ok) return;
        const j = await postForm('/admin/delete_user/' + encodeURIComponent(username), {});
        if (j && j.ok){ log(`deleted ${username}`); toast('ok','Deleted', username, 4500); }
        else toast('err','Delete failed', j && j.error ? j.error : 'unknown', 5200);
      }, 'danger');

      addAction(actSecurity, 'Reset password', async ()=>{
        const pw = await adminPrompt('Reset password', `Enter a new password for ${username}.`, {label:'New password', type:'password', autocomplete:'new-password', required:true, trim:false, minlength:ECAP_PASSWORD_MIN, maxlength:ECAP_PASSWORD_MAX, title:ECAP_PASSWORD_SUMMARY}, {confirmText:'Reset password', validate:(v)=> String(v.value || '').length < ECAP_PASSWORD_MIN ? `Minimum ${ECAP_PASSWORD_MIN} characters.` : (String(v.value || '').length > ECAP_PASSWORD_MAX ? `Maximum ${ECAP_PASSWORD_MAX} characters.` : '')});
        if (pw === null) return;
        if (pw.length < ECAP_PASSWORD_MIN) return toast('warn','Password too short',`Minimum ${ECAP_PASSWORD_MIN} characters`);
        if (pw.length > ECAP_PASSWORD_MAX) return toast('warn','Password too long',`Maximum ${ECAP_PASSWORD_MAX} characters`);
        const j = await postForm('/admin/reset_password/' + encodeURIComponent(username), {new_password: pw});
        if (j && j.ok){ log(`reset pw for ${username}`); toast('ok','Password reset', username); }
        else toast('err','Reset password failed', j && j.error ? j.error : 'unknown');
      }, 'primary');

      addAction(actSecurity, 'Set recovery PIN', async ()=>{
        const pin = await adminPrompt('Set recovery PIN', `Enter a new 4-8 digit recovery PIN for ${username}.`, {label:'New 4-8 digit PIN', inputmode:'numeric', maxlength:8, required:true}, {confirmText:'Set PIN', validate:(v)=> /^\d{4,8}$/.test(String(v.value || '').trim()) ? '' : 'PIN must be 4 to 8 digits.'});
        if (pin === null) return;
        if (!/^\d{4,8}$/.test(pin)) return toast('warn','Invalid PIN','PIN must be 4 to 8 digits');
        const j = await postForm('/admin/set_recovery_pin', {username, recovery_pin: pin});
        if (j && j.ok){ log(`set PIN for ${username}`); toast('ok','PIN updated', username); }
        else toast('err','PIN update failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actSecurity, 'Revoke 2FA', async ()=>{
        const ok = await adminConfirm('Revoke 2FA', `Revoke 2FA for ${username}?`, {danger:true, confirmText:'Revoke 2FA'});
        if (!ok) return;
        const j = await postForm('/admin/revoke_2fa/' + encodeURIComponent(username), {});
        if (j && j.ok){ log(`2FA revoked ${username}`); toast('ok','2FA revoked', username); loadUserDetail(username); }
        else toast('err','Revoke 2FA failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Suspend', async ()=>{
        const form = await adminPromptFields('Suspend user', `Suspend ${username}.`, [
          {name:'minutes', label:'Minutes', defaultValue:'60', inputmode:'numeric', required:true},
          {name:'reason', label:'Reason (optional)', type:'textarea', required:false}
        ], {confirmText:'Suspend', danger:true, validate:(v)=> { const n = parseInt(String(v.minutes || '').trim(),10); return (!isFinite(n) || n <= 0) ? 'Minutes must be a positive number.' : ''; }});
        if (!form) return;
        const mins = parseInt(String(form.minutes || '60').trim(),10) || 60;
        const reason = form.reason || '';
        const j = await postForm('/admin/suspend_user/' + encodeURIComponent(username), {minutes: mins, reason});
        if (j && j.ok){ log(`suspended ${username} for ${mins}m`); toast('ok','Suspended', `${username} • ${mins}m`); loadUserDetail(username); }
        else toast('err','Suspend failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Mute', async ()=>{
        const form = await adminPromptFields('Mute user', `Mute ${username}.`, [
          {name:'minutes', label:'Minutes', defaultValue:'15', inputmode:'numeric', required:true},
          {name:'reason', label:'Reason (optional)', type:'textarea', required:false}
        ], {confirmText:'Mute', validate:(v)=> { const n = parseInt(String(v.minutes || '').trim(),10); return (!isFinite(n) || n <= 0) ? 'Minutes must be a positive number.' : ''; }});
        if (!form) return;
        const mins = parseInt(String(form.minutes || '15').trim(),10) || 15;
        const reason = form.reason || '';
        const j = await postForm('/admin/mute_user/' + encodeURIComponent(username), {minutes: mins, reason});
        if (j && j.ok){ log(`muted ${username} for ${mins}m`); toast('ok','Muted', `${username} • ${mins}m`); loadUserDetail(username); }
        else toast('err','Mute failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Set quota', async ()=>{
        const raw = await adminPrompt('Set message quota', `Set messages per hour for ${username}. Use 0 for no custom quota.`, {label:'Messages per hour', defaultValue:'0', inputmode:'numeric', required:true}, {confirmText:'Set quota', validate:(v)=> { const n = parseInt(String(v.value || '').trim(),10); return (!isFinite(n) || n < 0) ? 'Quota must be 0 or higher.' : ''; }});
        if (raw === null) return;
        const q = parseInt(String(raw || '0').trim(),10);
        if (!isFinite(q) || q < 0) return toast('warn','Invalid number','Quota must be >= 0');
        const j = await postForm('/admin/set_user_quota/' + encodeURIComponent(username), {messages_per_hour: q});
        if (j && j.ok){ log(`quota ${username}=${q}`); toast('ok','Quota updated', `${username} • ${q}/hr`); loadUserDetail(username); }
        else toast('err','Quota update failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Set status', async ()=>{
        const form = await adminPromptFields('Set presence status', `Update presence for ${username}.`, [
          {name:'status', label:'Presence status', type:'select', options:[['online','online'], ['away','away'], ['busy','busy'], ['invisible','invisible']], required:true},
          {name:'custom_status', label:'Custom status (optional)', required:false}
        ], {confirmText:'Set status'});
        if (!form) return;
        const status = String(form.status || '').trim();
        const custom_status = String(form.custom_status || '').trim();
        if (!status) return;
        const j = await postForm('/admin/set_user_status/' + encodeURIComponent(username), {presence_status: status, custom_status});
        if (j && j.ok){ log(`status set ${username}=${status}`); toast('ok','Status updated', `${username} • ${status}`); loadUserDetail(username); }
        else toast('err','Status update failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Assign role', async ()=>{
        const role = await adminPrompt('Assign role', `Assign a role to ${username}.`, {label:'Role name', placeholder:'admin, moderator, user, viewer', required:true}, {confirmText:'Assign role'});
        if (role === null) return;
        if (!role) return;
        const j = await postForm('/admin/assign_role/' + encodeURIComponent(username), {role});
        if (j && j.ok){ log(`role assigned ${username} -> ${role}`); toast('ok','Role assigned', `${username} • ${role}`); loadUserDetail(username); }
        else toast('err','Assign role failed', j && j.error ? j.error : 'unknown');
      });

      addAction(actMod, 'Shadowban', async ()=>{
        const reason = await adminPrompt('Shadowban user', `Shadowban ${username}.`, {label:'Reason (optional)', type:'textarea', required:false}, {confirmText:'Shadowban', danger:true});
        if (reason === null) return;
        const j = await postForm('/admin/shadowban_user/' + encodeURIComponent(username), {reason});
        if (j && j.ok){ log(`shadowban ${username}`); toast('ok','Shadowbanned', username); loadUserDetail(username); }
        else toast('err','Shadowban failed', j && j.error ? j.error : 'unknown');
      });

      toast('info', 'User loaded', username);
    }

    // ROOMS
    buildRoomsSection(secRooms);

    const roomList = secRooms.querySelector('#ecapRoomList');
    const roomFilter = secRooms.querySelector('#ecapRoomFilter');
    let roomsCache = [];
    let roomsServerNowMs = 0;
    let roomsFetchedAtMs = 0;
    let roomsJanitorIntervalSeconds = 60;
    let roomsRefreshSeq = 0;

    function parseAdminTimeMs(v){
      const ms = v ? new Date(v).getTime() : NaN;
      return Number.isFinite(ms) ? ms : 0;
    }

    function roomsNowMs(){
      if (roomsServerNowMs > 0 && roomsFetchedAtMs > 0){
        return roomsServerNowMs + (Date.now() - roomsFetchedAtMs);
      }
      return Date.now();
    }

    function formatCountdownCompact(totalSeconds){
      let s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
      const d = Math.floor(s / 86400); s -= d * 86400;
      const h = Math.floor(s / 3600); s -= h * 3600;
      const m = Math.floor(s / 60); s -= m * 60;
      if (d > 0) return `${d}d ${h}h ${m}m`;
      if (h > 0) return `${h}h ${m}m ${s}s`;
      if (m > 0) return `${m}m ${s}s`;
      return `${s}s`;
    }

    function isCleanupManagedRoom(r){
      return !!(r && r.is_custom && String(r.room_kind || '').toLowerCase() === 'custom' && r.cleanup_managed !== false);
    }

    function customRoomExpirySummary(r){
      if (!isCleanupManagedRoom(r)) return '';
      const persistedCount = Number(r.persisted_member_count ?? r.member_count ?? r.members ?? r.count ?? 0) || 0;
      const liveRaw = Number(r.online_count ?? r.online ?? r.members_online);
      const liveKnown = Number.isFinite(liveRaw);
      const liveCount = liveKnown ? Math.max(0, liveRaw|0) : 0;
      const occupancyRaw = Number(r.cleanup_occupancy_count);
      const occupancyCount = Number.isFinite(occupancyRaw) ? Math.max(0, occupancyRaw|0) : (liveKnown ? liveCount : Math.max(0, persistedCount|0));
      const ttlMinutes = Math.max(0, Number(r.idle_ttl_minutes || 0) || 0);
      const activityMs = parseAdminTimeMs(r.activity_at || r.last_active_at || r.created_at);
      if (occupancyCount > 0){
        const source = liveKnown ? `live ${liveCount}` : `db ${persistedCount}`;
        const drift = liveKnown && persistedCount !== liveCount ? ` • db ${persistedCount}` : '';
        return `cleanup: occupied • timer paused until empty (${source}${drift})`;
      }
      if (!(ttlMinutes > 0) || !(activityMs > 0)){
        return 'cleanup: idle window unknown';
      }
      const remainingSeconds = Math.floor(((activityMs + (ttlMinutes * 60000)) - roomsNowMs()) / 1000);
      if (remainingSeconds <= 0){
        return `cleanup: eligible now • waiting for janitor (${Math.max(10, roomsJanitorIntervalSeconds|0)}s loop)`;
      }
      const staleNote = liveKnown && persistedCount > 0 ? ` • db stale ${persistedCount}` : '';
      return `cleanup: expires in ${formatCountdownCompact(remainingSeconds)} • idle window ${ttlMinutes}m${staleNote}`;
    }

    function updateRoomCountdowns(){
      roomList.querySelectorAll('[data-custom-room-expiry="1"]').forEach((node)=>{
        node.textContent = customRoomExpirySummary(node._ecapRoomRef || null);
      });
    }

    function renderRooms(){
      const f = (roomFilter.value||'').trim().toLowerCase();
      clearNode(roomList);
      const list = (roomsCache || []).filter(r => !f || String(r && r.name || '').toLowerCase().includes(f));
      if (!list.length){
        roomList.appendChild(listStatusNode('No rooms'));
        return;
      }
      list.forEach((r)=>{
        const row = el('div', {class:'ecap-item'});
        const left = el('div');
        left.style.minWidth = '0';
        const title = el('div', {text:r.name || ''});
        title.style.fontWeight = '780';
        title.style.whiteSpace = 'nowrap';
        title.style.overflow = 'hidden';
        title.style.textOverflow = 'ellipsis';
        left.appendChild(title);
        const online = (r && (r.online_count ?? r.online ?? r.members_online));
        const dbCount = (r && (r.persisted_member_count ?? r.member_count ?? r.members ?? r.count));
        const onlineNum = Number(online);
        const dbNum = Number(dbCount);
        const showOnline = Number.isFinite(onlineNum);
        const showDb = Number.isFinite(dbNum);

        let sub = '';
        if (showOnline){
          sub = `online: ${String(Math.max(0, onlineNum|0))}`;
          // If the persisted counter exists and differs, show it subtly for diagnostics.
          if (showDb && (dbNum|0) !== (onlineNum|0)){
            sub += ` • db: ${String(Math.max(0, dbNum|0))}`;
          }
        } else {
          sub = `members: ${String(Math.max(0, (dbNum|0) || 0))}`;
        }
        left.appendChild(mutedNode(sub));

        const controls = el('div');
        controls.style.display = 'flex';
        controls.style.gap = '6px';
        controls.style.alignItems = 'center';
        controls.style.flex = '0 0 auto';
        if (r.locked) controls.appendChild(pillNode('locked', 'bad'));
        if (r.readonly) controls.appendChild(pillNode('readonly', 'warn'));
        if (r.slowmode_sec && Number(r.slowmode_sec) > 0) controls.appendChild(pillNode(`slow ${Number(r.slowmode_sec)}s`, ''));
        if (r.is_custom) controls.appendChild(pillNode('custom', ''));
        if (r.is_custom && r.is_private) controls.appendChild(pillNode('private', 'warn'));
        const lockBtn = el('button', {class:'ecap-btn tight', 'data-act':'lock', type:'button', text:r.locked ? 'Unlock':'Lock'});
        const roBtn = el('button', {class:'ecap-btn tight', 'data-act':'ro', type:'button', text:r.readonly ? 'Writable':'Read-only'});
        const smBtn = el('button', {class:'ecap-btn tight', 'data-act':'sm', type:'button', text:'Slow'});
        controls.appendChild(lockBtn);
        controls.appendChild(roBtn);
        controls.appendChild(smBtn);
        const cleanupManaged = isCleanupManagedRoom(r);
        if (cleanupManaged) controls.appendChild(el('button', {class:'ecap-btn danger tight', 'data-act':'del', type:'button', text:'Delete'}));
        controls.appendChild(el('button', {class:'ecap-btn danger tight', 'data-act':'clear', type:'button', text:'Clear'}));
        row.appendChild(left);
        row.appendChild(controls);

        if (cleanupManaged){
          const expiryEl = el('div', {class:'ecap-muted'});
          expiryEl.setAttribute('data-custom-room-expiry', '1');
          expiryEl._ecapRoomRef = r;
          left.appendChild(expiryEl);
        }

        row.querySelector('[data-act="lock"]').addEventListener('click', (e)=>{
          e.stopPropagation();
          withAdminAction(e.currentTarget, `rooms:${r.name}:lock`, r.locked ? 'Unlocking' : 'Locking', async ()=>{
            const j = await postForm((r.locked ? '/admin/unlock_room/' : '/admin/lock_room/') + encodeURIComponent(r.name), {});
            if (j && j.ok){ log(`room ${r.name} lock=${!r.locked}`); toast('ok','Room updated', r.name); refreshRooms(); }
            else toast('err','Room update failed', j && j.error ? j.error : 'unknown');
          });
        });
        row.querySelector('[data-act="ro"]').addEventListener('click', (e)=>{
          e.stopPropagation();
          withAdminAction(e.currentTarget, `rooms:${r.name}:readonly`, 'Saving', async ()=>{
            const j = await postForm('/admin/set_room_readonly/' + encodeURIComponent(r.name), {readonly: r.readonly ? '0':'1'});
            if (j && j.ok){ log(`room ${r.name} readonly=${!r.readonly}`); toast('ok','Room updated', r.name); refreshRooms(); }
            else toast('err','Room update failed', j && j.error ? j.error : 'unknown');
          });
        });
        row.querySelector('[data-act="sm"]').addEventListener('click', (e)=>{
          e.stopPropagation();
          withAdminAction(e.currentTarget, `rooms:${r.name}:slowmode`, 'Setting', async ()=>{
            const cur = Number(r.slowmode_sec || 0) || 0;
            const raw = await adminPrompt('Set room slowmode', `Slowmode seconds for ${r.name}. Use 0 to disable.`, {label:'Seconds', defaultValue:String(cur), inputmode:'numeric', required:true}, {confirmText:'Set slowmode', validate:(v)=> { const n = parseInt(String(v.value || '').trim(),10); return (!isFinite(n) || n < 0 || n > 3600) ? 'Seconds must be between 0 and 3600.' : ''; }});
            if (raw === null) return;
            const seconds = Math.max(0, Math.min(3600, parseInt(String(raw).trim()||'0',10) || 0));
            const j = await postForm('/admin/set_room_slowmode/' + encodeURIComponent(r.name), {seconds: String(seconds)});
            if (j && j.ok){ log(`room slowmode ${r.name}=${seconds}`); toast('ok','Slowmode updated', `${r.name} • ${seconds}s`); refreshRooms(); }
            else toast('err','Slowmode update failed', j && j.error ? j.error : 'unknown');
          });
        });
        row.querySelector('[data-act="clear"]').addEventListener('click', (e)=>{
          e.stopPropagation();
          withAdminAction(e.currentTarget, `rooms:${r.name}:clear`, 'Clearing', async ()=>{
            const ok = await adminConfirm('Clear room messages', `Clear messages in ${r.name}?`, {danger:true, confirmText:'Clear'});
            if (!ok) return;
            const j = await postForm('/admin/clear_room/' + encodeURIComponent(r.name), {});
            if (j && j.ok){ log(`cleared room ${r.name}`); toast('ok','Room cleared', r.name); }
            else toast('err','Clear failed', j && j.error ? j.error : 'unknown');
          });
        });
        const delEl = row.querySelector('[data-act="del"]');
        if (delEl){
          delEl.addEventListener('click', (e)=>{
            e.stopPropagation();
            withAdminAction(e.currentTarget, `rooms:${r.name}:delete`, 'Deleting', async ()=>{
              const form = await adminPromptFields('Delete room', `Delete room "${r.name}"? This cannot be undone.`, [
                {name:'reason', label:'Reason (optional)', type:'textarea', required:false}
              ], {danger:true, confirmText:'Delete room'});
              if (!form) return;
              const reason = form.reason || '';
              const j = await postForm('/admin/rooms/delete/' + encodeURIComponent(r.name), {reason});
              if (j && j.ok){ log(`deleted room ${r.name}`); toast('ok','Room deleted', r.name, 4500); refreshRooms(); }
              else toast('err','Delete failed', (j && (j.message || j.error)) ? (j.message || j.error) : 'unknown');
            });
          });
        }

        row.addEventListener('click', ()=>{
          secRooms.querySelector('#ecapKRRoom').value = r.name;
        });
        roomList.appendChild(row);
      });
      updateRoomCountdowns();
    }

    async function refreshRooms(){
      const seq = ++roomsRefreshSeq;
      if (roomList) setListStatus(roomList, 'Loading rooms…');
      const j = await getJSON('/admin/rooms/list');
      if (seq !== roomsRefreshSeq) return;
      if (j && j.rooms){
        roomsCache = Array.isArray(j.rooms) ? j.rooms : [];
        roomsServerNowMs = parseAdminTimeMs(j.ts);
        roomsFetchedAtMs = Date.now();
        roomsJanitorIntervalSeconds = Math.max(10, Number(j.janitor_interval_seconds || 60) || 60);
        renderRooms();
        updateRoomCountdowns();
      } else if (roomList) {
        roomsCache = [];
        setListStatus(roomList, j && j.error ? j.error : 'Room list unavailable.');
      }
    }

    setInterval(updateRoomCountdowns, 1000);
    secRooms.querySelector('#ecapRoomsReload').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:reload', 'Loading', refreshRooms));
    roomFilter.addEventListener('input', debounce(renderRooms, 80));
    refreshRooms();

    // Room radio station editor
    const radioRoomSelect = secRooms.querySelector('#ecapRadioRoomSelect');
    const radioStationList = secRooms.querySelector('#ecapRadioStationList');
    const radioStatus = secRooms.querySelector('#ecapRadioStatus');
    let radioRoomsCache = [];
    let radioSelectedName = '';

    function currentRadioRoom(){
      const idx = parseInt(String(radioRoomSelect && radioRoomSelect.value || '0'), 10) || 0;
      return radioRoomsCache[idx] || null;
    }

    function stationRowValue(row){
      return {
        label: (row.querySelector('[data-radio-field="label"]')?.value || '').trim(),
        provider: (row.querySelector('[data-radio-field="provider"]')?.value || '').trim() || 'iHeartRadio',
        page_url: (row.querySelector('[data-radio-field="page_url"]')?.value || '').trim(),
        embed_url: (row.querySelector('[data-radio-field="embed_url"]')?.value || '').trim()
      };
    }

    function readStationRows(){
      return Array.from(radioStationList.querySelectorAll('[data-radio-station-row="1"]')).map(stationRowValue);
    }

    function addRadioStationRow(station){
      const st = station || {};
      const row = el('div', {class:'ecap-item ecap-radioStationRow', 'data-radio-station-row':'1'});
      const left = el('div', {class:'ecap-radioStationFields'});
      const top = gridNode('ecap-grid2', [
        el('input', {placeholder:'Station label', 'data-radio-field':'label', value: st.label || ''}),
        el('input', {placeholder:'Provider', 'data-radio-field':'provider', value: st.provider || 'iHeartRadio'})
      ], 'margin:0');
      const urls = gridNode('ecap-grid2', [
        el('input', {placeholder:'HTTPS source/page URL', 'data-radio-field':'page_url', value: st.page_url || ''}),
        el('input', {placeholder:'HTTPS embed/player URL', 'data-radio-field':'embed_url', value: st.embed_url || ''})
      ], 'margin-top:6px');
      left.appendChild(top);
      left.appendChild(urls);
      const actions = el('div', {class:'ecap-radioStationActions'});
      const up = el('button', {class:'ecap-btn tight', type:'button', 'data-act':'up', text:'↑'});
      const down = el('button', {class:'ecap-btn tight', type:'button', 'data-act':'down', text:'↓'});
      const remove = el('button', {class:'ecap-btn danger tight', type:'button', 'data-act':'remove', text:'Remove'});
      actions.appendChild(up);
      actions.appendChild(down);
      actions.appendChild(remove);
      row.appendChild(left);
      row.appendChild(actions);
      up.addEventListener('click', ()=>{
        const prev = row.previousElementSibling;
        if (prev) radioStationList.insertBefore(row, prev);
      });
      down.addEventListener('click', ()=>{
        const next = row.nextElementSibling;
        if (next) radioStationList.insertBefore(next, row);
      });
      remove.addEventListener('click', ()=>{
        row.remove();
        if (!radioStationList.querySelector('[data-radio-station-row="1"]')) setListStatus(radioStationList, 'No stations yet. Add one, then save.');
      });
      if (radioStationList.querySelector('.ecap-item span.ecap-muted')) clearNode(radioStationList);
      radioStationList.appendChild(row);
    }

    function renderRadioRoomOptions(){
      if (!radioRoomSelect) return;
      clearNode(radioRoomSelect);
      if (!radioRoomsCache.length){
        radioRoomSelect.appendChild(optionNode('0', 'No radio rooms found'));
        radioRoomSelect.disabled = true;
        return;
      }
      radioRoomSelect.disabled = false;
      radioRoomsCache.forEach((r, idx)=>{
        const label = `${r.category || 'Rooms'} › ${r.subcategory || 'All'} › ${r.name || 'Room'} (${Number(r.station_count || 0)} station${Number(r.station_count || 0) === 1 ? '' : 's'})`;
        radioRoomSelect.appendChild(optionNode(String(idx), label));
      });
      let selectedIdx = radioRoomsCache.findIndex(r => String(r.name || '') === radioSelectedName);
      if (selectedIdx < 0) selectedIdx = 0;
      radioRoomSelect.value = String(selectedIdx);
      radioSelectedName = String(radioRoomsCache[selectedIdx]?.name || '');
    }

    function renderRadioStationEditor(){
      clearNode(radioStationList);
      const room = currentRadioRoom();
      if (!room){
        setListStatus(radioStationList, 'No radio rooms found.');
        if (radioStatus) radioStatus.textContent = '';
        return;
      }
      radioSelectedName = String(room.name || '');
      if (radioStatus) radioStatus.textContent = `${room.category || 'Rooms'} › ${room.subcategory || 'All'} › ${room.name || ''}`;
      const stations = Array.isArray(room.stations) ? room.stations : [];
      if (!stations.length){
        setListStatus(radioStationList, 'No stations yet. Add one, then save.');
        return;
      }
      stations.forEach(addRadioStationRow);
    }

    async function refreshRadioCatalog(){
      const j = await getJSON('/admin/room_radio/catalog');
      if (j && j.ok){
        radioRoomsCache = Array.isArray(j.rooms) ? j.rooms : [];
        renderRadioRoomOptions();
        renderRadioStationEditor();
        log(`loaded ${radioRoomsCache.length} radio room station sets`);
      } else {
        radioRoomsCache = [];
        renderRadioRoomOptions();
        setListStatus(radioStationList, j && j.error ? j.error : 'Could not load radio rooms.');
      }
    }

    async function saveRadioStations(){
      const room = currentRadioRoom();
      if (!room || !room.name){
        toast('err','Radio save failed','Select a room first.');
        return;
      }
      const stations = readStationRows();
      const j = await postJSON('/admin/room_radio/' + encodeURIComponent(room.name) + '/stations', {stations});
      if (j && j.ok){
        toast('ok','Radio stations saved', `${room.name} • ${Number(j.station_count || 0)} station${Number(j.station_count || 0) === 1 ? '' : 's'}`);
        log(`radio stations saved for ${room.name}: ${Number(j.station_count || 0)}`);
        radioSelectedName = room.name;
        await refreshRadioCatalog();
      } else {
        toast('err','Radio save failed', j && j.error ? j.error : 'unknown', 6200);
      }
    }

    secRooms.querySelector('#ecapRadioReload')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:radio-reload', 'Loading', refreshRadioCatalog));
    secRooms.querySelector('#ecapRadioAddStation')?.addEventListener('click', ()=>{
      if (radioStationList.querySelector('.ecap-item span.ecap-muted')) clearNode(radioStationList);
      addRadioStationRow({provider:'iHeartRadio'});
    });
    secRooms.querySelector('#ecapRadioSave')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:radio-save', 'Saving', saveRadioStations));
    radioRoomSelect?.addEventListener('change', renderRadioStationEditor);
    refreshRadioCatalog();

    secRooms.querySelector('#ecapKickBtn').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:kick-user', 'Kicking', async ()=>{
      const username = (secRooms.querySelector('#ecapKRUser').value||'').trim();
      const room = (secRooms.querySelector('#ecapKRRoom').value||'').trim();
      if (!username || !room) return toast('warn','Missing fields','Enter username + room');
      const j = await postForm('/admin/kick_from_room', {username, room});
      if (j && j.ok){ log(`kicked ${username} from ${room}`); toast('ok','Kicked', `${username} • ${room}`); }
      else toast('err','Kick failed', j && j.error ? j.error : 'unknown');
    }));

    secRooms.querySelector('#ecapRoomBanBtn').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:ban-user', 'Banning', async ()=>{
      const username = (secRooms.querySelector('#ecapKRUser').value||'').trim();
      const room = (secRooms.querySelector('#ecapKRRoom').value||'').trim();
      if (!username || !room) return toast('warn','Missing fields','Enter username + room');
      const reason = await adminPrompt('Room ban reason', `Ban ${username} from ${room}.`, {label:'Reason (optional)', type:'textarea', required:false}, {confirmText:'Ban user', danger:true});
      if (reason === null) return;
      const j = await postForm('/admin/ban_from_room', {username, room, reason});
      if (j && j.ok){ log(`room ban ${username} in ${room}`); toast('ok','Room-banned', `${username} • ${room}`); }
      else toast('err','Room ban failed', j && j.error ? j.error : 'unknown');
    }));

    secRooms.querySelector('#ecapBroadcastBtn').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'rooms:broadcast', 'Sending', async ()=>{
      const msg = (secRooms.querySelector('#ecapBroadcast').value||'').trim();
      if (!msg) return toast('warn','Missing message','Enter a broadcast message');
      const j = await postForm('/admin/global_broadcast', {message: msg});
      if (j && j.ok){
        const deliveredSessions = Number(j.delivered || 0);
        const deliveredUsers = Number(j.delivered_users || 0);
        const deliveryText = `${deliveredSessions} client${deliveredSessions === 1 ? '' : 's'} / ${deliveredUsers} user${deliveredUsers === 1 ? '' : 's'} estimated`;
        log(`broadcast delivery estimate: ${deliveryText}`);
        toast('ok','Broadcast sent', deliveryText, 4500);
        secRooms.querySelector('#ecapBroadcast').value='';
      } else {
        toast('err','Broadcast failed', j && j.error ? j.error : 'unknown', 5200);
      }
    }));

    // VOICE
    buildVoiceSection(secVoice);

    // ECHO MEDIA / A-V
    buildAvSection(secAv);

    // SETTINGS
    buildSettingsSection(secSettings);

    buildSafetySection(secSafety);

    buildRolesSection(secRoles);

    const settingsForm = secSettings.querySelector('#ecapSettingsForm');
    let settingsCache = null;

    function makeField(label, id, type, hint){
      const c = el('div', {class:'ecap-card', style:'margin:0'});
      if (type === 'bool'){
        const row = el('div');
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.justifyContent = 'space-between';
        row.style.gap = '10px';
        const left = el('div');
        left.style.minWidth = '0';
        left.appendChild(mutedNode(label));
        row.appendChild(left);
        const inp = el('input', {id, type:'checkbox'});
        inp.style.width = 'auto';
        row.appendChild(inp);
        c.appendChild(row);
      } else if (type === 'animation') {
        c.appendChild(mutedNode(label));
        const inp = el('select', {id});
        [
          ['none', 'None — no text animation'],
          ['fade', 'Fade in'],
          ['rise', 'Fade + rise'],
          ['slide', 'Slide in'],
          ['scale', 'Soft scale']
        ].forEach(([value, text]) => inp.appendChild(el('option', {value, text})));
        c.appendChild(inp);
      } else if (type === 'textarea') {
        c.appendChild(mutedNode(label));
        const inp = el('textarea', {id, placeholder:'https://example.com/sound-pack.js'});
        inp.rows = 4;
        inp.style.minHeight = '92px';
        inp.style.resize = 'vertical';
        c.appendChild(inp);
      } else if (type === 'soundpack') {
        c.appendChild(mutedNode(label));
        const inp = el('select', {id});
        if (window.ecPopulateSoundPackSelect) {
          window.ecPopulateSoundPackSelect(inp, '');
        } else {
          [
            ['echo_modern_generated', 'Echo modern generated — 0001_echo_modern_generated.js'],
            ['classic_messenger_generated', 'Classic messenger generated — 0002_classic_messenger_generated.js']
          ].forEach(([value, text]) => inp.appendChild(el('option', {value, text})));
        }
        c.appendChild(inp);
      } else if (type === 'sound') {
        c.appendChild(mutedNode(label));
        const row = el('div', {class:'ecap-soundTestRow'});
        const inp = el('select', {id});
        if (window.ecPopulateSoundSelect) {
          window.ecPopulateSoundSelect(inp, '', {showFiles: true});
        } else {
          [
            ['soft_chime', 'Soft chime'],
            ['bubble_pop', 'Bubble pop'],
            ['glass_ping', 'Glass ping'],
            ['retro_blip', 'Retro blip'],
            ['muted_knock', 'Muted knock'],
            ['arcade_coin', 'Arcade coin'],
            ['mellow_pluck', 'Mellow pluck'],
            ['sonar_ping', 'Sonar ping'],
            ['digital_drop', 'Digital drop'],
            ['doorbell_duo', 'Doorbell duo'],
            ['page_flip', 'Page flip'],
            ['success_twinkle', 'Success twinkle'],
            ['warning_pulse', 'Warning pulse'],
            ['low_buzz', 'Low buzz'],
            ['classic_msg_ping', 'Classic messenger ping'],
            ['classic_msg_buzz', 'Classic messenger buzz'],
            ['classic_msg_knock', 'Classic messenger knock'],
            ['classic_msg_door', 'Classic messenger sign on/off'],
            ['classic_msg_mail', 'Classic messenger mail'],
            ['classic_msg_status', 'Classic messenger status']
          ].forEach(([value, text]) => inp.appendChild(el('option', {value, text})));
        }
        const btn = el('button', {type:'button', class:'ecap-btn tight ecap-soundTestBtn', text:'Test'});
        btn.title = 'Play the selected sound in this browser before saving.';
        btn.addEventListener('click', (ev) => {
          try { ev.preventDefault(); ev.stopPropagation(); } catch {}
          const kind = (id === 'set_sound_event_error') ? 'error' : ((id === 'set_sound_event_friend_request') ? 'ok' : 'test');
          if (window.ecTestSoundTheme) window.ecTestSoundTheme(inp.value, kind);
          else if (window.playUiSound) window.playUiSound(kind, {force:true, theme: inp.value});
        });
        row.appendChild(inp);
        row.appendChild(btn);
        c.appendChild(row);
      } else {
        c.appendChild(mutedNode(label));
        const inp = el('input', {id, placeholder:''});
        if (type !== 'text') inp.setAttribute('inputmode', 'numeric');
        c.appendChild(inp);
      }
      if (hint){
        const h = mutedNode(hint);
        h.style.marginTop = '6px';
        c.appendChild(h);
      }
      return c;
    }

    async function loadGeneralSettings(){
      const j = await getJSON('/admin/settings/general');
      settingsCache = (j && j.settings) ? j.settings : null;
      clearNode(settingsForm);
      if (!settingsCache){
        settingsForm.appendChild(mutedNode('Not available (requires admin).'));
        return;
      }
      const groups = [
        {
          title: 'Core features',
          desc: 'Main server features that turn large parts of the client on or off.',
          badge: 'features',
          fields: [
            ['Voice enabled','set_voice_enabled','bool',''],
            ['P2P file enabled','set_p2p_file_enabled','bool',''],
            ['Giphy enabled','set_giphy_enabled','bool',''],
            ['Disable file transfer (global)','set_disable_file_transfer_globally','bool','Master kill switch for all file-transfer surfaces.'],
            ['Disable DM files (global)','set_disable_dm_files_globally','bool','Stops server-stored encrypted direct-message files while leaving ordinary messages alone.'],
            ['Disable group files (global)','set_disable_group_files_globally','bool','Stops server-stored encrypted group files.']
          ]
        },
        {
          title: 'Security and privacy',
          desc: 'Encryption behavior for private messages, groups, private rooms, and sensitive profile fields.',
          badge: 'security',
          fields: [
            ['Require DM E2EE','set_require_dm_e2ee','bool','Encrypted-only private messages.'],
            ['Allow plaintext DM fallback','set_allow_plaintext_dm_fallback','bool','Temporary legacy mode only; enabling it disables required DM E2EE.'],
            ['Require group chat E2EE','set_require_group_e2ee','bool','Blocks plaintext group messages server-side.'],
            ['Allow legacy numeric group history','set_allow_legacy_numeric_group_history','bool','Off by default. Only enable temporarily to read old group rows stored under bare numeric room keys.'],
            ['Disable legacy group attachment upload','set_disable_legacy_group_file_upload','bool','Keeps old attachment-message upload path disabled; use encrypted group files instead.'],
            ['Require private-room E2EE','set_require_private_room_e2ee','bool','Blocks plaintext messages in invite-only/private custom rooms.'],
            ['Require all room E2EE','set_require_room_e2ee','bool','Strict mode: blocks plaintext in every room except supported slash commands. Requires impact acknowledgement because public-room text moderation/search becomes limited.'],
            ['Encrypt sensitive profile fields at rest','set_encrypt_sensitive_profile_fields','bool','Encrypts new phone/address/location writes when a server key is available.'],
            ['Privacy retention enabled','set_privacy_retention_enabled','bool','Hashes old IP/user-agent metadata after the retention window.'],
            ['IP/user-agent retention days','set_privacy_ip_user_agent_retention_days','int','Default 30. Set 0 only if you intentionally want no raw IP/UA retention sweep.'],
            ['Audit detail retention days','set_privacy_audit_detail_retention_days','int','Default 90. Scrubs old audit details that include ip= or ua= text.']
          ]
        },
        {
          title: 'Message display',
          desc: 'Controls how chat text appears to users. Classic sender labels repeat the sender name on every line instead of compact grouping.',
          badge: 'display',
          fields: [
            ['Chat room text animation','set_chat_text_animation','animation','Controls new text motion in public/custom room chat. Choose None to keep chat text steady. Reload client tabs after changing.'],
            ['Private message text animation','set_dm_text_animation','animation','Controls new text motion in private message windows.'],
            ['Group chat text animation','set_group_text_animation','animation','Controls new text motion in group chat windows.'],
            ['Room chat: show username on every message','set_room_show_sender_every_message','bool','Classic room display: public, custom, and private room chat show the sender name for every message. Reload client tabs after changing.'],
            ['Private messages: show username on every message','set_dm_show_sender_every_message','bool','Classic private-message display: every DM line repeats the sender name. Reload client tabs after changing.'],
            ['Group chat: show username on every message','set_group_show_sender_every_message','bool','Classic group display: every group line repeats the sender name. Reload client tabs after changing.']
          ]
        },
        {
          title: 'Notification sounds',
          desc: 'Server-wide defaults for JavaScript UI sounds. Online HTTPS .js sound packs load in the browser before the chat runtime parts; use Test beside each sound to hear it in this browser.',
          badge: 'sounds',
          fields: [
            ['Sound notifications on by default','set_sound_notifications_default','bool','Default for new browsers/users before they make a local choice.'],
            ['Online sound-pack .js URLs','set_sound_pack_external_urls','textarea','Optional. Put one HTTPS .js URL per line. Example: https://cdn.jsdelivr.net/npm/simple-notification-sounds@1.0.0/dist/simple-notification-sounds.umd.js. Save, hard-reload, then choose the pack/sounds.'],
            ['Also load local built-in sound packs','set_sound_pack_load_local_builtins','bool','Turn this off after your online sound-pack URLs are working if you do not want /static/js/sound_packs/*.js loaded from this server.'],
            ['Sound JavaScript file / pack','set_sound_pack_default','soundpack','Choose the default loaded sound pack. Event sound selectors below are grouped by their source JS file or URL. Reload client tabs after saving.'],
            ['General notification sound','set_sound_theme_default','sound','Fallback for ordinary toasts that do not have a specific event sound.'],
            ['Private message sound','set_sound_event_dm','sound','Used for new private messages and missed PM alerts.'],
            ['Room message sound','set_sound_event_room_message','sound','Used for room-message attention when the user is not actively focused on that room.'],
            ['Group message sound','set_sound_event_group_message','sound','Used for group-message attention.'],
            ['Room invite sound','set_sound_event_room_invite','sound','Used for public/custom room invites.'],
            ['Group invite sound','set_sound_event_group_invite','sound','Used for group invites.'],
            ['Friend request sound','set_sound_event_friend_request','sound','Used for new friend requests and accepted-request pings.'],
            ['Room join/leave sound','set_sound_event_room_join','sound','Used for room presence lines.'],
            ['File/torrent sound','set_sound_event_file','sound','Used for incoming shared files and torrents.'],
            ['Error/warning sound','set_sound_event_error','sound','Used for error toasts and warnings.']
          ]
        },
        {
          title: 'Limits and uploads',
          desc: 'Message length, file size ceilings, and group-message rate windows.',
          badge: 'limits',
          fields: [
            ['Max message length (chars)','set_max_message_length','int',''],
            ['Max attachment size (bytes)','set_max_attachment_size','int',''],
            ['Max DM file bytes','set_max_dm_file_bytes','int',''],
            ['Max group upload bytes','set_max_group_upload_bytes','int',''],
            ['Max torrent upload bytes','set_max_torrent_upload_bytes','int','Server-stored .torrent files only.'],
            ['Per-user file storage quota bytes','set_max_user_file_storage_bytes','int','Total encrypted DM/group file storage allowed per user. Set a finite value for public beta.'],
            ['Per-user torrent storage quota bytes','set_max_user_torrent_storage_bytes','int','Total server-stored .torrent file storage allowed per user.'],
            ['Max advertised torrent payload bytes','set_max_torrent_total_size_bytes','int','Rejects .torrent metadata claiming an oversized total payload.'],
            ['Enable torrent uploads','set_torrent_upload_enabled','bool','Allows room users to attach server-stored .torrent files.'],
            ['Enable tracker scrape','set_torrent_scrape_enabled','bool','Allows arbitrary user-supplied tracker URLs after SSRF validation. Takes effect immediately; use caution for public beta.'],
            ['Enable public fallback scrape','set_torrent_public_fallback_scrape_enabled','bool',"Allows Echo-Chat's configured public fallback trackers for trackerless torrents/magnets even when arbitrary tracker scrape is off."],
            ['Enable DHT swarm lookup','set_torrent_dht_scrape_enabled','bool','Allows bounded best-effort DHT peer/seed lookup when tracker scrape returns no numbers.'],
            ['DHT timeout seconds','set_torrent_dht_scrape_timeout_sec','float','Bounded per-query timeout; runtime clamps to a small safe range.'],
            ['DHT max queries','set_torrent_dht_scrape_max_queries','int','Maximum DHT nodes queried during a best-effort swarm lookup.'],
            ['Public fallback trackers','set_torrent_public_fallback_trackers','textarea','Optional. One udp/http/https tracker announce URL per line. Credentials and invalid schemes are rejected.'],
            ['Group msg rate limit','set_group_msg_rate_limit','int','messages per window'],
            ['Group msg window seconds','set_group_msg_rate_window_sec','int','']
          ]
        },
        {
          title: 'Presence and inactivity',
          desc: 'Automatic away/offline behavior while a user is still signed in.',
          badge: 'presence',
          fields: [
            ['Auto-away after inactive minutes','set_presence_idle_minutes','int','After this many inactive minutes, the user stays signed in but shows Away. Set 0 to disable.'],
            ['Auto-offline after inactive minutes','set_presence_offline_minutes','int','After this many inactive minutes, the user stays signed in but switches to Invisible so other users see them as offline. Set 0 to disable.']
          ]
        },
        {
          title: 'Room autosplit',
          desc: 'Controls when public room overflow shards are created. Example: with capacity 30, clicking Introductions joins Introductions until it has 30 users, then joins Introductions (2).',
          badge: 'autosplit',
          fields: [
            ['Enable public room autosplit','set_autoscale_rooms_enabled','bool','When on, full public rooms create overflow rooms like Introductions (2) automatically.'],
            ['Users per room before split','set_autoscale_room_capacity','int','Normal default is 30. Test Lab can temporarily force this lower during autosplit diagnostics.'],
            ['Overflow room cleanup minutes','set_autoscale_room_idle_minutes','int','How long empty autosplit overflow rooms like Introductions (2) stay before cleanup.']
          ]
        },
        {
          title: 'Room cleanup',
          desc: 'Background janitor timing for empty custom rooms.',
          badge: 'cleanup',
          fields: [
            ['Custom room idle minutes (public)','set_custom_room_idle_minutes','int','Empty custom rooms are auto-deleted after this many minutes (180 = 3 hours by default).'],
            ['Custom room idle minutes (private)','set_custom_private_room_idle_minutes','int','Private rooms are also 180 minutes by default, but admins can change it.'],
            ['Janitor interval (seconds)','set_janitor_interval_seconds','int','How often cleanup runs (10..3600).']
          ]
        }
      ];

      function appendSettingsGroup(group){
        const wrap = el('div', {class:'ecap-settingsGroup' + (group.badge === 'limits' ? ' badge-risk' : '')});
        const head = el('div', {class:'ecap-settingsGroupHead'});
        const titleWrap = el('div');
        titleWrap.appendChild(el('div', {class:'ecap-settingsGroupTitle', text: group.title || 'Settings'}));
        if (group.desc) titleWrap.appendChild(el('div', {class:'ecap-settingsGroupDesc', text: group.desc}));
        head.appendChild(titleWrap);
        if (group.badge) head.appendChild(pillNode(group.badge, ''));
        wrap.appendChild(head);
        if (group.badge === 'sounds') {
          const docRow = rowNode([], 'margin:10px 0 2px;flex-wrap:wrap');
          const guideBtn = buttonNode('ecapOpenSoundPackGuide', 'Open sound-pack guide', 'ecap-btn tight');
          const sourceBtn = buttonNode('ecapOpenSoundSources', 'Open sound source list', 'ecap-btn tight');
          const cdnBtn = buttonNode('ecapCopySimpleSoundCdn', 'Copy SNS CDN URL', 'ecap-btn tight');
          guideBtn.title = 'Open the local admin Markdown guide for online JavaScript sound packs.';
          sourceBtn.title = 'Open the local admin list of places to review chat/UI sounds.';
          cdnBtn.title = 'Copy the tested Simple Notification Sounds CDN URL to paste into Online sound-pack .js URLs.';
          guideBtn.addEventListener('click', () => window.open('/admin/docs/online-sound-packs', '_blank', 'noopener,noreferrer'));
          sourceBtn.addEventListener('click', () => window.open('/admin/docs/online-chat-sound-sources', '_blank', 'noopener,noreferrer'));
          cdnBtn.addEventListener('click', (e) => withAdminAction(e.currentTarget, 'settings:sound-cdn-copy', 'Copying', async () => {
            const url = 'https://cdn.jsdelivr.net/npm/simple-notification-sounds@1.0.0/dist/simple-notification-sounds.umd.js';
            try {
              await navigator.clipboard.writeText(url);
              toast('ok', 'Sound URL copied', 'Paste it into Online sound-pack .js URLs, save, then hard-reload.');
            } catch {
              toast('warn', 'Copy failed', url, 7000);
            }
          }));
          docRow.appendChild(guideBtn);
          docRow.appendChild(sourceBtn);
          docRow.appendChild(cdnBtn);
          wrap.appendChild(docRow);
        }
        const grid = el('div', {class:'ecap-grid2'});
        for (const [label,key,typ,hint] of (group.fields || [])){
          const id = key;
          grid.appendChild(makeField(label, id, typ, hint));
          const realKey = key.replace('set_','');
          const v = settingsCache[realKey];
          const inp = grid.querySelector('#'+id);
          if (!inp) continue;
          if (typ === 'bool') inp.checked = !!v;
          else if (typ === 'animation') inp.value = String(v || (realKey === 'chat_text_animation' ? 'none' : 'rise'));
          else if (typ === 'textarea') inp.value = Array.isArray(v) ? v.join('\n') : ((v === null || v === undefined) ? '' : String(v));
          else if (typ === 'soundpack') {
            if (window.ecPopulateSoundPackSelect) window.ecPopulateSoundPackSelect(inp, String(v || 'echo_modern_generated'));
            inp.value = String(v || 'echo_modern_generated');
          }
          else if (typ === 'sound') {
            if (window.ecPopulateSoundSelect) window.ecPopulateSoundSelect(inp, String(v || 'soft_chime'), {showFiles: true});
            inp.value = String(v || 'soft_chime');
          }
          else inp.value = (v === null || v === undefined) ? '' : String(v);
        }
        wrap.appendChild(grid);
        settingsForm.appendChild(wrap);
      }

      groups.forEach(appendSettingsGroup);
      const summary = secSettings.querySelector('#ecapSettingsSummary');
      if (summary){
        clearNode(summary);
        const fileOff = !!settingsCache.disable_file_transfer_globally;
        const dmOff = !!settingsCache.disable_dm_files_globally;
        const groupOff = !!settingsCache.disable_group_files_globally;
        const torrentOn = !!settingsCache.torrent_upload_enabled;
        const scrapeOn = !!settingsCache.torrent_scrape_enabled;
        const fallbackScrapeOn = !!settingsCache.torrent_public_fallback_scrape_enabled;
        const dhtScrapeOn = !!settingsCache.torrent_dht_scrape_enabled;
        const fileQuota = Number(settingsCache.max_user_file_storage_bytes || 0);
        const torrentQuota = Number(settingsCache.max_user_torrent_storage_bytes || 0);
        summary.appendChild(pillNode(fileOff ? 'files: off' : 'files: on', fileOff ? 'warn' : 'ok'));
        summary.appendChild(pillNode((dmOff || groupOff) ? `dm/group: ${dmOff ? 'dm off' : 'dm on'} · ${groupOff ? 'group off' : 'group on'}` : 'dm/group: on', (dmOff || groupOff) ? 'warn' : 'ok'));
        summary.appendChild(pillNode(`torrent: ${torrentOn ? 'upload on' : 'upload off'} · scrape ${scrapeOn ? 'on' : 'off'} · fallback ${fallbackScrapeOn ? 'on' : 'off'} · DHT ${dhtScrapeOn ? 'on' : 'off'}`, scrapeOn ? 'warn' : (torrentOn ? 'ok' : '')));
        summary.appendChild(pillNode(`quotas: files ${fileQuota ? 'set' : 'missing'} · torrents ${torrentQuota ? 'set' : 'missing'}`, (fileQuota && torrentQuota) ? 'ok' : 'warn'));
      }
    }

    async function applyGeneralSettings(){
      if (!settingsCache) return toast('warn','Not available','Requires admin');
      const payload = {};
      function grabBool(key){
        const id = 'set_'+key;
        const elx = secSettings.querySelector('#'+id);
        if (!elx) return;
        payload[key] = !!elx.checked;
      }
      function grabInt(key){
        const id = 'set_'+key;
        const elx = secSettings.querySelector('#'+id);
        if (!elx) return;
        const v = (elx.value||'').trim();
        if (v === '') return;
        payload[key] = parseInt(v,10);
      }
      function grabFloat(key){
        const id = 'set_'+key;
        const elx = secSettings.querySelector('#'+id);
        if (!elx) return;
        const v = (elx.value||'').trim();
        if (v === '') return;
        payload[key] = parseFloat(v);
      }
      function grabText(key){
        const id = 'set_'+key;
        const elx = secSettings.querySelector('#'+id);
        if (!elx) return;
        const v = (elx.value||'').trim();
        if (v === '') return;
        payload[key] = v;
      }

      ['voice_enabled','p2p_file_enabled','giphy_enabled','disable_file_transfer_globally','disable_dm_files_globally','disable_group_files_globally','torrent_upload_enabled','torrent_scrape_enabled','torrent_public_fallback_scrape_enabled','torrent_dht_scrape_enabled','require_dm_e2ee','allow_plaintext_dm_fallback','require_group_e2ee','allow_legacy_numeric_group_history','disable_legacy_group_file_upload','require_private_room_e2ee','require_room_e2ee','all_room_e2ee_impact_acknowledged','encrypt_sensitive_profile_fields','privacy_retention_enabled','privacy_ip_user_agent_retention_days','privacy_audit_detail_retention_days','room_show_sender_every_message','dm_show_sender_every_message','group_show_sender_every_message','sound_notifications_default','sound_pack_load_local_builtins','autoscale_rooms_enabled'].forEach(grabBool);
      ['max_message_length','max_attachment_size','max_dm_file_bytes','max_group_upload_bytes','max_torrent_upload_bytes','max_user_file_storage_bytes','max_user_torrent_storage_bytes','max_torrent_total_size_bytes','torrent_dht_scrape_max_queries','group_msg_rate_limit','group_msg_rate_window_sec','presence_idle_minutes','presence_offline_minutes','custom_room_idle_minutes','custom_private_room_idle_minutes','janitor_interval_seconds','autoscale_room_capacity','autoscale_room_idle_minutes'].forEach(grabInt);
      ['torrent_dht_scrape_timeout_sec'].forEach(grabFloat);
      ['chat_text_animation','dm_text_animation','group_text_animation','sound_pack_default','sound_theme_default','sound_event_dm','sound_event_room_message','sound_event_group_message','sound_event_room_invite','sound_event_group_invite','sound_event_friend_request','sound_event_room_join','sound_event_file','sound_event_error'].forEach(grabText);
      const soundPackUrls = secSettings.querySelector('#set_sound_pack_external_urls');
      if (soundPackUrls) payload.sound_pack_external_urls = String(soundPackUrls.value || '');
      const fallbackTrackers = secSettings.querySelector('#set_torrent_public_fallback_trackers');
      if (fallbackTrackers) payload.torrent_public_fallback_trackers = String(fallbackTrackers.value || '');
      if (payload.require_room_e2ee === true && !(settingsCache && settingsCache.require_room_e2ee)){
        const ok = await adminConfirm('Require all room E2EE strict mode', 'Public-room message text will be encrypted, so server-side body moderation/search/transcript inspection will be limited.', {danger:true, confirmText:'Enable strict mode'});
        if (!ok) return;
        payload.confirm_all_room_e2ee_impact = true;
        payload.all_room_e2ee_impact_acknowledged = true;
      }

      const j = await postJSON('/admin/settings/general', payload);
      if (j && j.ok){
        log(`settings patch persisted=${j.persisted}`);
        toast('ok','Settings applied', `persisted=${j.persisted}`);
        loadGeneralSettings();
        refreshStats();
      } else {
        toast('err','Settings apply failed', j && j.error ? j.error : 'unknown', 5200);
      }
    }

    secSettings.querySelector('#ecapSettingsReload').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'settings:reload', 'Loading', loadGeneralSettings));
    secSettings.querySelector('#ecapSettingsApply').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'settings:apply', 'Saving', applyGeneralSettings));
    loadGeneralSettings();

    // GIF SETTINGS (GIPHY)
    const giphyKeyInput = secSettings.querySelector('#ecapGiphyKey');
    const giphyShowBtn = secSettings.querySelector('#ecapGiphyShow');
    const giphyStatus = secSettings.querySelector('#ecapGiphyKeyStatus');
    const giphyRating = secSettings.querySelector('#ecapGiphyRating');
    const giphyLang = secSettings.querySelector('#ecapGiphyLang');
    const giphyLimit = secSettings.querySelector('#ecapGiphyLimit');

    async function loadGifSettings(){
      const j = await getJSON('/admin/settings/gifs');
      if (!j || !j.ok){
        if (giphyStatus) giphyStatus.textContent = 'unavailable';
        return;
      }
      if (giphyStatus) giphyStatus.textContent = j.has_key ? 'set' : 'missing';
      if (giphyRating) giphyRating.value = String(j.giphy_rating || 'pg-13');
      if (giphyLang) giphyLang.value = String(j.giphy_lang || 'en');
      if (giphyLimit) giphyLimit.value = String(j.giphy_default_limit || 24);
      if (giphyKeyInput) giphyKeyInput.value = '';
    }

    async function applyGifSettings(){
      const payload = {};
      if (giphyRating) payload.giphy_rating = (giphyRating.value||'').trim() || 'pg-13';
      if (giphyLang) payload.giphy_lang = (giphyLang.value||'').trim() || 'en';
      if (giphyLimit){
        const v = (giphyLimit.value||'').trim();
        if (v !== '') payload.giphy_default_limit = parseInt(v,10);
      }
      if (giphyKeyInput){
        const k = (giphyKeyInput.value||'').trim();
        if (k !== '') payload.giphy_api_key = k;
      }
      const j = await postJSON('/admin/settings/gifs', payload);
      if (j && j.ok){
        toast('ok','GIF settings saved', `persisted=${j.persisted} key=${j.has_key?'set':'missing'}`);
        loadGifSettings();
        refreshStats();
      } else {
        toast('err','GIF settings failed', (j && j.error) ? j.error : 'unknown', 5200);
      }
    }

    if (giphyShowBtn && giphyKeyInput){
      giphyShowBtn.addEventListener('click', ()=>{
        const isPw = giphyKeyInput.type === 'password';
        giphyKeyInput.type = isPw ? 'text' : 'password';
        giphyShowBtn.textContent = isPw ? 'Hide' : 'Show';
      });
    }

    secSettings.querySelector('#ecapGiphyReload')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'giphy:reload', 'Loading', loadGifSettings));
    secSettings.querySelector('#ecapGiphyApply')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'giphy:apply', 'Saving', applyGifSettings));
    loadGifSettings();

    // ANTI-ABUSE SETTINGS
    const antiForm = secSafety.querySelector('#ecapAntiForm');
    let antiCache = null;

    async function loadAntiAbuseSettings(){
      const j = await getJSON('/admin/settings/antiabuse');
      antiCache = (j && j.settings) ? j.settings : null;
      clearNode(antiForm);
      if (!antiCache){
        antiForm.appendChild(mutedNode('Not available (requires admin).'));
        return;
      }
      const fields = [
        ['Room msg rate limit','anti_room_msg_rate_limit','text','Format: "N@seconds" (example 20@10)'],
        ['Room msg window seconds','anti_room_msg_rate_window_sec','int',''],
        ['DM msg rate limit','anti_dm_msg_rate_limit','text','Format: "N@seconds"'],
        ['DM msg window seconds','anti_dm_msg_rate_window_sec','int',''],
        ['Room typing indicators','anti_enable_room_typing_indicators','bool','Default off; room chat typing indicators stay disabled unless enabled here'],
        ['DM typing indicators','anti_enable_dm_typing_indicators','bool','Shows "user is typing" in private message windows'],
        ['Group typing indicators','anti_enable_group_typing_indicators','bool','Shows typing status in group chat windows'],
        ['DM typing rate limit','anti_dm_typing_rate_limit','text','Format: "N@seconds"; protects PM typing loops'],
        ['DM typing window seconds','anti_dm_typing_rate_window_sec','int',''],
        ['Group typing rate limit','anti_group_typing_rate_limit','text','Format: "N@seconds"; protects group typing loops'],
        ['Group typing window seconds','anti_group_typing_rate_window_sec','int',''],
        ['File offer rate limit','anti_file_offer_rate_limit','text','Format: "N@seconds"'],
        ['File offer window seconds','anti_file_offer_rate_window_sec','int',''],
        ['Room GIF rate limit','anti_room_gif_rate_limit','text','Format: "N@seconds"'],
        ['Room GIF window seconds','anti_room_gif_rate_window_sec','int',''],
        ['Room torrent rate limit','anti_room_torrent_rate_limit','text','Format: "N@seconds"'],
        ['Room torrent window seconds','anti_room_torrent_rate_window_sec','int',''],
        ['Typing event rate limit','anti_room_typing_rate_limit','text','Format: "N@seconds"; protects typing/stop-typing loops'],
        ['Typing window seconds','anti_room_typing_rate_window_sec','int',''],
        ['Reaction rate limit','anti_room_reaction_rate_limit','text','Format: "N@seconds"; protects reaction spam'],
        ['Reaction window seconds','anti_room_reaction_rate_window_sec','int',''],
        ['Room media action rate limit','anti_room_media_action_rate_limit','text','Format: "N@seconds"; protects radio source changes and skip votes'],
        ['Room media action window seconds','anti_room_media_action_rate_window_sec','int',''],
        ['Room media presence rate limit','anti_room_media_presence_rate_limit','text','Format: "N@seconds"; protects listener heartbeat spam'],
        ['Room media presence window seconds','anti_room_media_presence_rate_window_sec','int',''],
        ['Room catalog rate limit','anti_room_catalog_rate_limit','text','Format: "N@seconds"; protects repeated room-list loads'],
        ['Room catalog window seconds','anti_room_catalog_rate_window_sec','int',''],
        ['Room counts rate limit','anti_room_counts_rate_limit','text','Format: "N@seconds"; protects repeated count polling'],
        ['Room counts window seconds','anti_room_counts_rate_window_sec','int',''],
        ['Wave user rate limit','anti_wave_user_rate_limit','text','Format: "N@seconds"; protects harassment by waves'],
        ['Wave user window seconds','anti_wave_user_rate_window_sec','int',''],
        ['Room control rate limit','anti_room_control_rate_limit','text','Format: "N@seconds"; protects pin/unpin and retired room-control loops'],
        ['Room control window seconds','anti_room_control_rate_window_sec','int',''],
        ['Default room slowmode (sec)','anti_room_slowmode_default_sec','int',''],
        ['Strikes before auto-mute','anti_antiabuse_strikes_before_mute','int',''],
        ['Strike window (sec)','anti_antiabuse_strike_window_sec','int',''],
        ['Auto-mute minutes','anti_antiabuse_auto_mute_minutes','int',''],
        ['Join rate limit','anti_room_join_rate_limit','text','Format: "N@seconds"'],
        ['Join window seconds','anti_room_join_rate_window_sec','int',''],
        ['Room switch cooldown seconds','anti_room_switch_cooldown_sec','int','Minimum seconds between switching from one active room to another'],
        ['Room create rate limit','anti_room_create_rate_limit','text','Format: "N@seconds"'],
        ['Room create window seconds','anti_room_create_rate_window_sec','int',''],
        ['Allow users to create rooms','anti_allow_user_create_rooms','bool',''],
        ['Max room name length','anti_max_room_name_length','int',''],
        ['Enable blocked custom room terms','anti_block_custom_room_terms_enabled','bool','Reject custom room names that match blocked terms after casefolding, Unicode normalization, and simple punctuation/leet cleanup'],
        ['Blocked custom room terms','anti_blocked_custom_room_terms','text','Extra comma-separated or newline-separated terms to block (added on top of the built-in defaults)'],
        ['Enable blocked registration usernames','anti_block_registration_terms_enabled','bool','Reject registration usernames that match reserved words or blocked terms after casefolding, Unicode normalization, and simple punctuation/leet cleanup'],
        ['Blocked registration username terms','anti_blocked_registration_terms','text','Extra comma-separated or newline-separated terms to block in new usernames (added on top of the built-in defaults)'],
        ['Friend request rate limit','anti_friend_req_rate_limit','text','Format: "N@seconds"'],
        ['Friend request window seconds','anti_friend_req_rate_window_sec','int',''],
        ['Friend unique targets max','anti_friend_req_unique_targets_max','int',''],
        ['Friend unique targets window','anti_friend_req_unique_targets_window_sec','int',''],
        ['Max links per message','anti_max_links_per_message','int',''],
        ['Max magnets per message','anti_max_magnets_per_message','int',''],
        ['Max mentions per message','anti_max_mentions_per_message','int',''],
        ['Dup msg window (sec)','anti_dup_msg_window_sec','int',''],
        ['Dup msg max repeats','anti_dup_msg_max','int',''],
        ['Dup msg min length','anti_dup_msg_min_length','int',''],
        ['Normalize dup compare','anti_dup_msg_normalize','bool','Lowercase + collapse spaces']
      ];

      for (const [label,key,typ,hint] of fields){
        antiForm.appendChild(makeField(label, key, typ, hint));
        const realKey = key.replace('anti_','');
        const v = antiCache[realKey];
        const inp = secSafety.querySelector('#'+key);
        if (!inp) continue;
        if (typ === 'bool') inp.checked = !!v;
        else inp.value = (v === null || v === undefined) ? '' : String(v);
      }
    }

    async function applyAntiAbuseSettings(){
      if (!antiCache) return toast('warn','Not available','Requires admin');
      const payload = {};
      function grabBool(realKey){
        const id = 'anti_'+realKey;
        const elx = secSafety.querySelector('#'+id);
        if (!elx) return;
        payload[realKey] = !!elx.checked;
      }
      function grabInt(realKey){
        const id = 'anti_'+realKey;
        const elx = secSafety.querySelector('#'+id);
        if (!elx) return;
        const v = (elx.value||'').trim();
        if (v === '') return;
        payload[realKey] = parseInt(v,10);
      }
      function grabText(realKey){
        const id = 'anti_'+realKey;
        const elx = secSafety.querySelector('#'+id);
        if (!elx) return;
        const v = (elx.value||'').trim();
        if (v === '') return;
        payload[realKey] = v;
      }

      ['allow_user_create_rooms','dup_msg_normalize','block_custom_room_terms_enabled','block_registration_terms_enabled','enable_room_typing_indicators','enable_dm_typing_indicators','enable_group_typing_indicators'].forEach(grabBool);
      [
        'room_msg_rate_window_sec','dm_msg_rate_window_sec','dm_typing_rate_window_sec','group_typing_rate_window_sec','file_offer_rate_window_sec','room_gif_rate_window_sec','room_torrent_rate_window_sec',
        'room_typing_rate_window_sec','room_reaction_rate_window_sec','room_media_action_rate_window_sec','room_media_presence_rate_window_sec',
        'room_catalog_rate_window_sec','room_counts_rate_window_sec','wave_user_rate_window_sec','room_control_rate_window_sec','room_slowmode_default_sec',
        'antiabuse_strikes_before_mute','antiabuse_strike_window_sec','antiabuse_auto_mute_minutes',
        'room_join_rate_window_sec','room_switch_cooldown_sec','room_create_rate_window_sec','max_room_name_length',
        'friend_req_rate_window_sec','friend_req_unique_targets_max','friend_req_unique_targets_window_sec',
        'max_links_per_message','max_magnets_per_message','max_mentions_per_message',
        'dup_msg_window_sec','dup_msg_max','dup_msg_min_length'
      ].forEach(grabInt);
      [
        'room_msg_rate_limit','dm_msg_rate_limit','dm_typing_rate_limit','group_typing_rate_limit','file_offer_rate_limit','room_gif_rate_limit','room_torrent_rate_limit',
        'room_typing_rate_limit','room_reaction_rate_limit','room_media_action_rate_limit','room_media_presence_rate_limit',
        'room_catalog_rate_limit','room_counts_rate_limit','wave_user_rate_limit','room_control_rate_limit',
        'room_join_rate_limit','room_create_rate_limit','friend_req_rate_limit','blocked_custom_room_terms','blocked_registration_terms'
      ].forEach(grabText);

      const j = await postJSON('/admin/settings/antiabuse', payload);
      if (j && j.ok){
        log(`antiabuse patch persisted=${j.persisted}`);
        toast('ok','Anti-abuse applied', `persisted=${j.persisted}`);
        loadAntiAbuseSettings();
      } else {
        toast('err','Anti-abuse apply failed', j && j.error ? j.error : 'unknown', 5200);
      }
    }

    secSafety.querySelector('#ecapAntiReload').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'anti-abuse:reload', 'Loading', loadAntiAbuseSettings));
    secSafety.querySelector('#ecapAntiApply').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'anti-abuse:apply', 'Saving', applyAntiAbuseSettings));
    loadAntiAbuseSettings();

    secSafety.querySelector('#ecapBanIpBtn')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'safety:ban-ip', 'Banning', async ()=>{
      const ip = (secSafety.querySelector('#ecapBanIpAddress')?.value || '').trim();
      const reason = (secSafety.querySelector('#ecapBanIpReason')?.value || '').trim() || 'Manual IP ban';
      if (!ip) return toast('warn', 'Missing IP', 'Enter an IPv4 or IPv6 address first');
      const ok = await adminConfirm('Ban IP address', `Ban ${ip}? Matching active sessions/tokens will be revoked.`, {danger:true, confirmText:'Ban IP'});
      if (!ok) return;
      const j = await postForm('/admin/ban_ip', {ip, reason});
      if (j && j.ok){
        const sessions = Number(j.revoked_sessions || 0);
        const tokens = Number(j.revoked_tokens || 0);
        toast('ok', 'IP banned', `${j.ip || ip} • ${sessions} session(s), ${tokens} token(s)`);
        log(`ip banned ${j.ip || ip}; sessions=${sessions}; tokens=${tokens}`);
        secSafety.querySelector('#ecapBanIpAddress').value = '';
        secSafety.querySelector('#ecapBanIpReason').value = '';
        refreshModeration();
        refreshStats();
      } else {
        toast('err', 'IP ban failed', j && (j.message || j.error) ? (j.message || j.error) : 'unknown', 5200);
      }
    }));

    async function refreshIncidentMode(){
      const j = await getJSON('/admin/incident_mode');
      const node = secSafety.querySelector('#ecapIncidentStatus');
      if (!node) return;
      const incident = (j && j.incident) ? j.incident : {};
      const mode = incident.mode || 'off';
      node.textContent = `Incident mode: ${mode}${incident.enabled ? ' • active' : ''}`;
      node.className = `ecap-pill ${incident.enabled ? 'warn' : 'ok'}`;
    }
    secSafety.querySelector('#ecapIncidentApply').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'safety:incident-apply', 'Applying', async ()=>{
      const mode = (secSafety.querySelector('#ecapIncidentPreset').value || '').trim();
      const persist = !!secSafety.querySelector('#ecapIncidentPersist').checked;
      const j = await postForm('/admin/incident_mode/apply', {mode, persist: persist ? '1' : '0'});
      if (j && j.ok){
        toast('ok', 'Incident mode applied', mode);
        refreshIncidentMode();
        refreshModeration();
        refreshStats();
      } else {
        toast('err', 'Incident mode failed', j && j.error ? j.error : 'unknown');
      }
    }));
    secSafety.querySelector('#ecapIncidentDisable').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'safety:incident-disable', 'Disabling', async ()=>{
      const persist = !!secSafety.querySelector('#ecapIncidentPersist').checked;
      const j = await postForm('/admin/incident_mode/disable', {persist: persist ? '1' : '0'});
      if (j && j.ok){
        toast('ok', 'Incident mode disabled', 'Controls relaxed');
        refreshIncidentMode();
        refreshModeration();
        refreshStats();
      } else {
        toast('err', 'Disable failed', j && j.error ? j.error : 'unknown');
      }
    }));
    refreshIncidentMode();

    const rolesState = {roles: [], permissions: [], currentRole: null};
    let rolesRefreshSeq = 0;
    let rolePermissionSeq = 0;
    async function refreshRolesUI(){
      const seq = ++rolesRefreshSeq;
      const rolesResp = await getJSON('/admin/roles');
      const permsResp = await getJSON('/admin/permissions');
      if (seq !== rolesRefreshSeq) return;
      rolesState.roles = (rolesResp && rolesResp.roles) ? rolesResp.roles : [];
      rolesState.permissions = (permsResp && permsResp.permissions) ? permsResp.permissions : [];
      const list = secRoles.querySelector('#ecapRolesList');
      const cloneSel = secRoles.querySelector('#ecapRoleCloneSource');
      const explainSel = secRoles.querySelector('#ecapExplainPermission');
      if (cloneSel){
        clearNode(cloneSel);
        rolesState.roles.forEach(r=>{
          const o = document.createElement('option');
          o.value = r.name; o.textContent = r.name; cloneSel.appendChild(o);
        });
      }
      if (explainSel){
        clearNode(explainSel);
        rolesState.permissions.forEach(meta=>{
          const o = document.createElement('option');
          o.value = meta.name; o.textContent = meta.name; explainSel.appendChild(o);
        });
      }
      if (list){
        clearNode(list);
        if (!rolesState.roles.length){
          list.appendChild(listStatusNode('No roles returned.'));
        }
        rolesState.roles.forEach(r=>{
          const row = el('div', {class:'ecap-item'});
          const info = el('div');
          info.style.minWidth = '0';
          const title = el('div', {text:r.name || ''});
          title.style.fontWeight = '750';
          if (r.protected){
            const protectedPill = pillNode('protected', 'warn');
            protectedPill.style.marginLeft = '6px';
            title.appendChild(document.createTextNode(' '));
            title.appendChild(protectedPill);
          }
          info.appendChild(title);
          info.appendChild(mutedNode(`members: ${String(r.member_count || 0)} • permissions: ${String(r.permission_count || 0)}`));
          row.appendChild(info);
          const btns = el('div', {class:'ecap-actions'});
          const inspect = el('button', {class:'ecap-btn tight', text:'Inspect', type:'button'});
          inspect.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `roles:${r.name}:inspect`, 'Loading', ()=>selectRole(r.name)));
          btns.appendChild(inspect);
          if (!r.protected){
            const del = el('button', {class:'ecap-btn danger tight', text:'Delete', type:'button'});
            del.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `roles:${r.name}:delete`, 'Deleting', async ()=>{
              const j = await postForm('/admin/role/delete', {name: r.name});
              if (j && j.ok){ toast('ok', 'Role deleted', r.name); refreshRolesUI(); }
              else toast('err', 'Delete role failed', j && j.error ? j.error : 'unknown');
            }));
            btns.appendChild(del);
          }
          row.appendChild(btns);
          list.appendChild(row);
        });
      }
      if (!rolesState.currentRole && rolesState.roles.length) selectRole(rolesState.roles[0].name);
    }
    async function selectRole(roleName){
      const seq = ++rolePermissionSeq;
      rolesState.currentRole = roleName;
      const head = secRoles.querySelector('#ecapRolesCurrentRole');
      if (head) head.textContent = `Role: ${roleName}`;
      const wrap = secRoles.querySelector('#ecapRolePermissions');
      if (!wrap) return;
      const roleResp = await getJSON('/admin/role/' + encodeURIComponent(roleName) + '/permissions');
      if (seq !== rolePermissionSeq || rolesState.currentRole !== roleName) return;
      const current = new Set((roleResp && roleResp.permissions) ? roleResp.permissions : []);
      clearNode(wrap);
      rolesState.permissions.forEach(meta=>{
        const row = el('div', {class:'ecap-item'});
        const checked = current.has(meta.name);
        const label = el('label');
        label.style.display = 'flex';
        label.style.gap = '10px';
        label.style.alignItems = 'flex-start';
        label.style.width = '100%';
        const cb = el('input', {type:'checkbox'});
        cb.checked = !!checked;
        const info = el('div');
        info.style.minWidth = '0';
        const name = el('div', {text:meta.name || ''});
        name.style.fontWeight = '750';
        if (meta.dangerous){
          name.appendChild(document.createTextNode(' '));
          name.appendChild(pillNode('danger', 'bad'));
        }
        info.appendChild(name);
        info.appendChild(mutedNode(`${meta.category || 'Other'} • ${meta.description || ''}`));
        label.appendChild(cb);
        label.appendChild(info);
        row.appendChild(label);
        cb.addEventListener('change', async ()=>{
          const desired = !!cb.checked;
          cb.disabled = true;
          cb.setAttribute('aria-busy', 'true');
          const url = desired ? '/admin/role/add_permission' : '/admin/role/remove_permission';
          try{
            const j = await postForm(url, {role: roleName, permission: meta.name});
            if (j && j.ok){ toast('ok', desired ? 'Permission added' : 'Permission removed', `${roleName} • ${meta.name}`); }
            else { cb.checked = !desired; toast('err', 'Permission update failed', j && j.error ? j.error : 'unknown'); }
          } finally {
            cb.disabled = false;
            cb.removeAttribute('aria-busy');
          }
        });
        wrap.appendChild(row);
      });
    }
    secRoles.querySelector('#ecapRoleCreate').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'roles:create', 'Creating', async ()=>{
      const name = (secRoles.querySelector('#ecapNewRoleName').value || '').trim().toLowerCase();
      if (!name) return toast('warn', 'Missing role name', 'Enter a role name first');
      const j = await postForm('/admin/role/create', {name});
      if (j && j.ok){ toast('ok', 'Role created', name); secRoles.querySelector('#ecapNewRoleName').value = ''; refreshRolesUI(); }
      else toast('err', 'Create role failed', j && j.error ? j.error : 'unknown');
    }));
    secRoles.querySelector('#ecapRoleClone').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'roles:clone', 'Cloning', async ()=>{
      const source = (secRoles.querySelector('#ecapRoleCloneSource').value || '').trim();
      const name = (secRoles.querySelector('#ecapRoleCloneName').value || '').trim().toLowerCase();
      if (!source || !name) return toast('warn', 'Missing clone fields', 'Choose a source role and clone name');
      const j = await postForm('/admin/role/clone', {source, name});
      if (j && j.ok){ toast('ok', 'Role cloned', `${source} → ${name}`); secRoles.querySelector('#ecapRoleCloneName').value = ''; refreshRolesUI(); }
      else toast('err', 'Clone failed', j && j.error ? j.error : 'unknown');
    }));
    secRoles.querySelector('#ecapRolesLoadUser').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'roles:load-user', 'Loading', async ()=>{
      const username = (secRoles.querySelector('#ecapRolesUser').value || '').trim();
      if (!username) return toast('warn', 'Missing username', 'Enter a username first');
      const [rolesResp, permsResp] = await Promise.all([
        getJSON('/admin/user/' + encodeURIComponent(username) + '/roles'),
        getJSON('/admin/user/' + encodeURIComponent(username) + '/permissions')
      ]);
      const summary = secRoles.querySelector('#ecapRolesUserSummary');
      const perms = secRoles.querySelector('#ecapRolesUserPermissions');
      if (summary) clearNode(summary);
      if (perms) clearNode(perms);
      const roleList = (rolesResp && rolesResp.roles) ? rolesResp.roles : [];
      const permList = (permsResp && permsResp.permissions) ? permsResp.permissions : [];
      if (summary){
        if (!roleList.length) summary.appendChild(listStatusNode('No roles assigned.'));
        roleList.forEach(roleName=>{
          const row = el('div', {class:'ecap-item'});
          const info = el('div');
          info.style.minWidth = '0';
          const title = el('div', {text:roleName || ''});
          title.style.fontWeight = '750';
          info.appendChild(title);
          row.appendChild(info);
          if (!['admin','moderator','viewer'].includes(roleName)){
            const btn = el('button', {class:'ecap-btn danger tight', text:'Remove', type:'button'});
            btn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, `roles:user:${username}:${roleName}:remove`, 'Removing', async ()=>{
              const j = await postForm('/admin/user/' + encodeURIComponent(username) + '/remove_role', {role: roleName});
              if (j && j.ok){ toast('ok', 'Role removed', `${username} • ${roleName}`); secRoles.querySelector('#ecapRolesLoadUser').click(); }
              else toast('err', 'Remove role failed', j && j.error ? j.error : 'unknown');
            }));
            row.appendChild(btn);
          }
          summary.appendChild(row);
        });
      }
      if (perms){
        if (!permList.length) perms.appendChild(listStatusNode('No effective permissions returned.'));
        permList.forEach(name=>{
          const row = el('div', {class:'ecap-item'});
          const info = el('div');
          info.style.minWidth = '0';
          const title = el('div', {text:name || ''});
          title.style.fontWeight = '750';
          info.appendChild(title);
          row.appendChild(info);
          perms.appendChild(row);
        });
      }
    }));
    secRoles.querySelector('#ecapExplainPermissionBtn')?.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'roles:explain', 'Checking', async ()=>{
      const username = (secRoles.querySelector('#ecapRolesUser').value || '').trim();
      const permission = (secRoles.querySelector('#ecapExplainPermission').value || '').trim();
      const out = secRoles.querySelector('#ecapPermissionExplain');
      if (!username || !permission) return toast('warn', 'Missing fields', 'Enter a username and choose a permission');
      const j = await getJSON('/admin/permission/explain?' + new URLSearchParams({username, permission}).toString());
      if (j && j.ok){
        const roles = (j.matched_roles || []).join(', ') || 'no matching roles';
        if (out) out.textContent = `${username} ${j.allowed ? 'has' : 'does not have'} ${permission}; source: ${roles}.`;
        toast(j.allowed ? 'ok' : 'warn', 'Permission explained', j.explanation || permission);
      } else {
        if (out) out.textContent = j && j.error ? j.error : 'Explain failed.';
        toast('err', 'Explain failed', j && j.error ? j.error : 'unknown');
      }
    }));
    refreshRolesUI();

    // AUDIT
    buildAuditSection(secAudit);

    const auditQ = secAudit.querySelector('#ecapAuditQ');
    const auditActor = secAudit.querySelector('#ecapAuditActor');
    const auditAction = secAudit.querySelector('#ecapAuditAction');
    const auditTarget = secAudit.querySelector('#ecapAuditTarget');
    const auditList = secAudit.querySelector('#ecapAuditList');

    async function refreshAudit(){
      const q = (auditQ.value||'').trim();
      const actor = (auditActor.value||'').trim();
      const action = (auditAction.value||'').trim();
      const target = (auditTarget.value||'').trim();
      const qs = new URLSearchParams({q, actor, action, target, limit:'80'}).toString();
      const j = await getJSON('/admin/audit/recent?'+qs);
      clearNode(auditList);
      const ev = (j && j.events) ? j.events : [];
      if (!ev.length){
        auditList.appendChild(listStatusNode('No events'));
        return;
      }
      for (const e of ev){
        const row = el('div', {class:'ecap-item'});
        const ts = e.timestamp ? new Date(e.timestamp).toLocaleString() : '';
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div');
        title.style.fontWeight = '750';
        title.style.whiteSpace = 'nowrap';
        title.style.overflow = 'hidden';
        title.style.textOverflow = 'ellipsis';
        title.appendChild(document.createTextNode(e.action || ''));
        title.appendChild(document.createTextNode(' '));
        title.appendChild(el('span', {class:'ecap-muted', text:`(${ts})`}));
        const actor = mutedNode(`actor: ${e.actor || ''} • target: ${e.target || '—'}`);
        actor.style.whiteSpace = 'nowrap'; actor.style.overflow = 'hidden'; actor.style.textOverflow = 'ellipsis';
        const details = mutedNode(e.details || '');
        details.style.whiteSpace = 'nowrap'; details.style.overflow = 'hidden'; details.style.textOverflow = 'ellipsis';
        info.appendChild(title);
        info.appendChild(actor);
        info.appendChild(details);
        row.appendChild(info);
        auditList.appendChild(row);
      }
    }
    secAudit.querySelector('#ecapAuditRefresh').addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'audit:refresh', 'Loading', refreshAudit));
    [auditQ, auditActor, auditAction, auditTarget].forEach(inp=>inp.addEventListener('input', debounce(refreshAudit, 260)));
    refreshAudit();

    // Make user list items draggable (best-effort; external DOM)
    function enableDraggableUsers(){
      const lists = [document.getElementById('userList'), document.getElementById('friendsList')].filter(Boolean);
      for (const ul of lists){
        ul.querySelectorAll('li').forEach(li=>{
          if (li.getAttribute('data-ecap-draggable') === '1') return;
          const text = (li.textContent||'').trim();
          if (!text) return;
          li.setAttribute('draggable','true');
          li.setAttribute('data-ecap-draggable','1');
          li.addEventListener('dragstart', (e)=>{
            e.dataTransfer.setData('text/plain', text.split('\n')[0].trim());
          });
        });
      }
    }
    setInterval(enableDraggableUsers, 1500);

    // Stats refresh
    async function refreshStats(){
      const j = await getJSON('/admin/stats');
      if (!j || j.ok === false){
        dot.classList.remove('ok'); dot.classList.add('bad');
        toast('err','Admin API unavailable', (j && j.error) ? j.error : 'unknown', 5200);
        return;
      }
      dot.classList.remove('bad'); dot.classList.add('ok');

      const set = (id, val)=>{ const n = document.getElementById(id); if (n) n.textContent = String(val ?? '—'); };
      set('ecapStatOnline', j.online_users ?? '—');
      set('ecapStatUsers', j.registered_users ?? '—');
      set('ecapStatRooms', j.rooms ?? '—');
      set('ecapStatSessions', j.connected_sessions ?? (j.online_usernames ? j.online_usernames.length : '—'));
      set('ecapStatUptime', j.uptime_seconds != null ? fmtUptime(j.uptime_seconds) : '—');
      set('ecapStatPg', j.postgres_version ?? '—');
      set('ecapStatNow', j.server_time ?? '—');

      const vRooms = document.getElementById('ecapVoiceRooms');
      const vUsers = document.getElementById('ecapVoiceUsers');
      if (vRooms) vRooms.textContent = String(j.voice_rooms ?? '—');
      if (vUsers) vUsers.textContent = String(j.voice_total_users ?? '—');

      // Online list
      const onlineWrap = document.getElementById('ecapOnlineList');
      if (onlineWrap){
        clearNode(onlineWrap);
        const users = (j.online_usernames || []).slice(0, 24);
        if (!users.length){
          onlineWrap.appendChild(mutedNode('No live roster available.', 'span'));
        } else {
          for (const u of users){
            const b = el('button', {class:'ecap-btn tight', text:u, type:'button'});
            b.addEventListener('click', ()=>{
              setTargetUser(u, {syncInput:true, loadDetail:true});
              setTab('users');
            });
            onlineWrap.appendChild(b);
          }
          if (j.online_usernames.length > users.length){
            const more = el('span', {class:'ecap-muted', text:`+${j.online_usernames.length-users.length} more`});
            onlineWrap.appendChild(more);
          }
        }
      }

      // Feature pills
      const pillWrap = document.getElementById('ecapFeaturePills');
      if (pillWrap){
        clearNode(pillWrap);
        const snap = j.settings_snapshot || {};
        const mk = (label, ok, cls)=>{
          const s = el('span', {class:`ecap-pill ${cls || (ok?'ok':'bad')}`, text: label});
          pillWrap.appendChild(s);
        };
        mk(`voice ${snap.voice_enabled ? 'on':'off'}`, !!snap.voice_enabled);
        mk(`giphy ${snap.giphy_enabled ? 'on':'off'}`, !!snap.giphy_enabled);
        mk(`p2p ${snap.p2p_file_enabled ? 'on':'off'}`, !!snap.p2p_file_enabled);
        mk(`health ${snap.health_endpoint_enabled ? 'on':'off'}`, !!snap.health_endpoint_enabled, snap.health_endpoint_enabled ? 'ok' : 'warn');
        mk(`webcam ${snap.webcam_enabled ? 'on':'off'}`, !!snap.webcam_enabled, snap.webcam_enabled ? 'ok' : 'warn');
        if (snap.voice_max_room_peers != null){
          const lim = snap.voice_max_room_peers ? snap.voice_max_room_peers : '∞';
          mk(`voice cap ${lim}`, true, 'warn');
        }
      }
    }

    function renderMetricGrid(host, metrics){
      if (!host) return;
      clearNode(host);
      if (!metrics || !metrics.length){
        host.appendChild(mutedNode('Analytics unavailable.'));
        return;
      }
      metrics.forEach(m=>{
        const card = el('div', {class:'ecap-metric'});
        card.appendChild(el('div', {class:'lbl', text:m.label || 'metric'}));
        card.appendChild(el('div', {class:'val', text:String(m.value ?? '—')}));
        card.appendChild(el('div', {class:'meta', text:m.meta || ''}));
        host.appendChild(card);
      });
    }

    function renderBarChart(host, rows, opts){
      if (!host) return;
      clearNode(host);
      const items = Array.isArray(rows) ? rows : [];
      if (!items.length){
        host.appendChild(mutedNode('No chart data yet.'));
        return;
      }
      const max = Math.max(1, ...items.map(r=>Number(r && r.count) || 0));
      const cls = (opts && opts.barClass) ? String(opts.barClass) : '';
      items.forEach((row, idx)=>{
        const count = Number(row && row.count) || 0;
        const label = String((row && row.label) || idx + 1);
        const h = Math.max(4, Math.round((count / max) * 100));
        const col = el('div', {class:'ecap-barCol'});
        col.appendChild(el('div', {class:'ecap-barVal', text:String(count)}));
        const wrap = el('div', {class:'ecap-barWrap'});
        const bar = el('div', {class:`ecap-bar ${cls}`.trim(), title:`${label} • ${String(count)}`});
        bar.style.height = `${h}%`;
        wrap.appendChild(bar);
        col.appendChild(wrap);
        col.appendChild(el('div', {class:'ecap-barLbl', title:label, text:label}));
        host.appendChild(col);
      });
    }

    function renderSimpleList(host, rows, valueKey, emptyText){
      if (!host) return;
      clearNode(host);
      const items = Array.isArray(rows) ? rows : [];
      if (!items.length){
        host.appendChild(listStatusNode(emptyText || 'Nothing yet.'));
        return;
      }
      items.forEach(row=>{
        const label = String(row && (row.label || row.action || row.name || row.room || 'item'));
        const value = row && (row[valueKey] != null ? row[valueKey] : row.count);
        const item = el('div', {class:'ecap-item'});
        const info = el('div');
        info.style.minWidth = '0';
        const title = el('div', {text:label});
        title.style.fontWeight = '750';
        title.style.whiteSpace = 'nowrap';
        title.style.overflow = 'hidden';
        title.style.textOverflow = 'ellipsis';
        info.appendChild(title);
        const metaText = String(row && (row.meta || row.note || '') || '').trim();
        if (metaText){
          const meta = el('div', {class:'ecap-muted', text:metaText});
          meta.style.fontSize = '11px';
          meta.style.marginTop = '3px';
          meta.style.whiteSpace = 'normal';
          info.appendChild(meta);
        }
        item.appendChild(info);
        item.appendChild(pillNode(String(value ?? 0), 'warn'));
        host.appendChild(item);
      });
    }

    function mediaModeLabel(mode){
      const m = String(mode || 'echo').toLowerCase();
      if (m === 'echo' || m === 'webrtc' || m === 'built_in' || m === 'builtin') return 'echo';
      return 'standard';
    }

    function renderMediaStatus(j){
      const decision = (j && j.decision) || {};
      const settings = (j && j.settings) || j || {};
      const requested = String(settings.av_mode || decision.requested_mode || 'echo');
      const active = String(settings.active_mode || decision.mode || 'echo');
      const features = decision.features || {};
      const setText = (id, txt)=>{ const n = document.getElementById(id); if (n) n.textContent = txt; };
      setText('ecapAvRequested', mediaModeLabel(requested));
      setText('ecapAvActive', mediaModeLabel(active));
      setText('ecapAvWebcam', settings.webcam_enabled || features.webcam ? 'yes' : 'no');
      setText('ecapMediaTransport', settings.webcam_transport || 'echo-webrtc-mesh');
      const modeSel = document.getElementById('ecapAvModeSelect');
      if (modeSel && !modeSel.matches(':focus')) modeSel.value = mediaModeLabel(requested) === 'echo' ? 'echo' : 'standard';
      const webcamEnabled = document.getElementById('ecapWebcamEnabled');
      if (webcamEnabled) webcamEnabled.checked = !!(settings.webcam_enabled || features.webcam);
      const q = document.getElementById('ecapWebcamQuality');
      if (q && !q.matches(':focus')) q.value = String(settings.webcam_quality || 'balanced');
      const codec = document.getElementById('ecapWebcamCodecStrategy');
      if (codec && !codec.matches(':focus')) codec.value = String(settings.webcam_codec_strategy || 'prefer-compatible');
      const policy = settings.webcam_policy || decision.webcam_policy || {};
      const camPolicy = document.getElementById('ecapWebcamPolicy');
      if (camPolicy && !camPolicy.matches(':focus')) camPolicy.value = String(settings.webcam_approval_mode || policy.webcam_approval_mode || 'owner_approval');
      const maxViewers = document.getElementById('ecapWebcamMaxViewers');
      if (maxViewers && !maxViewers.matches(':focus')) maxViewers.value = String(settings.webcam_max_viewers != null ? settings.webcam_max_viewers : (policy.webcam_max_viewers != null ? policy.webcam_max_viewers : 0));
      const defaultMedia = document.getElementById('ecapDefaultMediaPolicy');
      if (defaultMedia && !defaultMedia.matches(':focus')) defaultMedia.value = String(settings.default_media_policy || policy.default_media_policy || 'user_choice');
      const pills = document.getElementById('ecapMediaPills');
      if (pills){
        clearNode(pills);
        const activeEcho = mediaModeLabel(active) === 'echo';
        pills.appendChild(pillNode(`active: ${mediaModeLabel(active)}`, activeEcho?'ok':'warn'));
        pills.appendChild(pillNode(`webcam: ${settings.webcam_enabled || features.webcam ? 'enabled' : 'disabled'}`, settings.webcam_enabled || features.webcam ? 'ok' : 'warn'));
        pills.appendChild(pillNode(`quality: ${settings.webcam_quality || 'balanced'}`, ''));
        pills.appendChild(pillNode(`codec: ${settings.webcam_codec_strategy || 'prefer-compatible'}`, ''));
        pills.appendChild(pillNode(`cam policy: ${settings.webcam_approval_mode || policy.webcam_approval_mode || 'owner_approval'}`, (settings.webcam_approval_mode || policy.webcam_approval_mode) === 'disabled' ? 'bad' : 'ok'));
        pills.appendChild(pillNode(`transport: ${settings.webcam_transport || 'echo-webrtc-mesh'}`, 'warn'));
      }
      const list = document.getElementById('ecapMediaChecks');
      if (list){
        clearNode(list);
        const rows = [
          ['Built-in WebRTC', 'ok', 'Browser getUserMedia + RTCPeerConnection are used for webcam/mic.'],
          ['Scaling note', 'warn', 'Mesh transport is best for small webcam groups. For large public rooms, keep webcam owner-approval on and low/balanced quality defaults.']
        ];
        rows.forEach(([name, st, summary])=>{
          const row = el('div', {class:'ecap-item'});
          const info = el('div');
          info.style.minWidth = '0';
          const title = el('div', {text:name});
          title.style.fontWeight = '750';
          const badge = pillNode(st, st === 'ok' ? 'ok' : 'warn');
          badge.style.marginLeft = '6px';
          title.appendChild(document.createTextNode(' '));
          title.appendChild(badge);
          info.appendChild(title);
          info.appendChild(mutedNode(summary));
          row.appendChild(info);
          list.appendChild(row);
        });
      }
    }

    async function refreshMediaStatus(){
      const j = await getJSON('/admin/settings/media');
      if (!j || j.ok === false){
        toast('err', 'Media status failed', j && j.error ? j.error : 'unknown', 5200);
        return;
      }
      renderMediaStatus(j);
    }

    async function applyMediaSettings(){
      const payload = {
        av_mode: (document.getElementById('ecapAvModeSelect')?.value || 'echo').trim(),
        webcam_enabled: !!document.getElementById('ecapWebcamEnabled')?.checked,
        webcam_quality: (document.getElementById('ecapWebcamQuality')?.value || 'balanced').trim(),
        webcam_codec_strategy: (document.getElementById('ecapWebcamCodecStrategy')?.value || 'prefer-compatible').trim(),
        webcam_approval_mode: (document.getElementById('ecapWebcamPolicy')?.value || 'owner_approval').trim(),
        webcam_max_viewers: (document.getElementById('ecapWebcamMaxViewers')?.value || '0').trim(),
        default_media_policy: (document.getElementById('ecapDefaultMediaPolicy')?.value || 'user_choice').trim(),
      };
      const j = await postJSON('/admin/settings/media', payload);
      if (j && j.ok){
        toast('ok', 'Echo media saved', `mode=${mediaModeLabel(j.av_mode)} • webcam=${j.webcam_enabled ? 'on' : 'off'} • quality=${j.webcam_quality || 'balanced'}`);
        renderMediaStatus(j);
        refreshMediaStatus();
      } else {
        toast('err', 'Media settings failed', j && j.error ? j.error : 'unknown', 5200);
      }
    }



    async function refreshAnalytics(){
      const j = await getJSON('/admin/analytics/overview');
      const summaryHost = document.getElementById('ecapAnalyticsSummary');
      const auditChart = document.getElementById('ecapAuditChart');
      const sanctionsChart = document.getElementById('ecapSanctionsChart');
      const topActions = document.getElementById('ecapTopActions');
      const topRooms = document.getElementById('ecapTopRooms');
      const topActors = document.getElementById('ecapTopActors');
      const topTargets = document.getElementById('ecapTopTargets');
      const generatedAt = document.getElementById('ecapAnalyticsGeneratedAt');
      if (!j || j.ok === false){
        renderMetricGrid(summaryHost, []);
        renderBarChart(auditChart, []);
        renderBarChart(sanctionsChart, []);
        renderSimpleList(topActions, [], 'count', 'Analytics unavailable.');
        renderSimpleList(topRooms, [], 'count', 'Analytics unavailable.');
        renderSimpleList(topActors, [], 'count', 'Analytics unavailable.');
        renderSimpleList(topTargets, [], 'count', 'Analytics unavailable.');
        if (generatedAt) generatedAt.textContent = 'generated: unavailable';
        return;
      }
      const s = j.summary || {};
      renderMetricGrid(summaryHost, [
        {label:'Audit events', value:s.audit_events_24h ?? 0, meta:'last 24 hours'},
        {label:'Sanctions created', value:s.sanctions_24h ?? 0, meta:'last 24 hours'},
        {label:'Room actions', value:s.room_actions_24h ?? 0, meta:'lock / readonly / slowmode / clear'},
        {label:'Active sanctions', value:s.active_sanctions ?? 0, meta:`incident changes 7d: ${s.incidents_7d ?? 0}`},
      ]);
      renderBarChart(auditChart, j.hourly_audit || []);
      renderBarChart(sanctionsChart, j.daily_sanctions || [], {barClass:'warn'});
      renderSimpleList(topActions, j.actions_7d || [], 'count', 'No admin actions in the last 7 days.');
      renderSimpleList(topRooms, j.top_rooms_live || [], 'count', 'No live room pressure right now.');
      renderSimpleList(topActors, j.top_actors_7d || [], 'count', 'No actor data yet.');
      renderSimpleList(topTargets, j.top_targets_7d || [], 'count', 'No target data yet.');
      if (generatedAt) generatedAt.textContent = `generated: ${j.generated_at || '—'}`;
    }

    function renderSecurityStatus(j){
      const overall = document.getElementById('ecapSecurityOverall');
      const when = document.getElementById('ecapSecurityWhen');
      const list = document.getElementById('ecapSecurityChecks');
      if (!j || j.ok === false){
        if (overall){ overall.textContent = 'security: unavailable'; overall.className = 'ecap-pill bad'; }
        if (when){ when.textContent = 'checked: failed'; when.className = 'ecap-pill warn'; }
        if (list) setListStatus(list, 'Security status endpoint unavailable.');
        return;
      }
      const state = String(j.overall || 'unknown');
      if (overall){
        const warnCount = Array.isArray(j.warnings) ? j.warnings.length : 0;
        overall.textContent = `security: ${state}${warnCount ? ` • ${warnCount} warning${warnCount === 1 ? '' : 's'}` : ''}`;
        overall.className = `ecap-pill ${state === 'ok' ? 'ok' : state === 'fail' ? 'bad' : 'warn'}`;
      }
      if (when){
        when.textContent = `checked: ${j.generated_at || '—'}`;
        when.className = 'ecap-pill';
      }
      if (list){
        clearNode(list);
        const checks = Array.isArray(j.checks) ? j.checks : [];
        if (!checks.length){
          list.appendChild(listStatusNode('No security checks returned.'));
        } else {
          checks.forEach(ch=>{
            const row = el('div', {class:'ecap-item'});
            const info = el('div');
            info.style.minWidth = '0';
            const title = el('div', {text:String(ch.label || ch.key || 'check')});
            title.style.fontWeight = '750';
            const badge = pillNode(String(ch.level || (ch.ok ? 'ok' : 'warn')), ch.level === 'ok' ? 'ok' : ch.level === 'bad' ? 'bad' : 'warn');
            badge.style.marginLeft = '6px';
            title.appendChild(document.createTextNode(' '));
            title.appendChild(badge);
            info.appendChild(title);
            info.appendChild(mutedNode(String(ch.summary || '')));
            row.appendChild(info);
            list.appendChild(row);
          });
          const enc = j.encrypted_profile_counts || {};
          const emailEnc = j.encrypted_email_counts || {};
          const backups = Array.isArray(j.security_backups) ? j.security_backups : [];
          const retention = j.privacy_retention || {};
          const meta = el('div', {class:'ecap-item'});
          const metaInfo = el('div');
          metaInfo.style.minWidth = '0';
          metaInfo.appendChild(el('div', {text:'Profile encryption / privacy retention'}));
          metaInfo.appendChild(mutedNode(`encrypted phone/address/location rows: ${enc.phone_encrypted ?? '—'} / ${enc.address_encrypted ?? '—'} / ${enc.location_encrypted ?? '—'} • plaintext: ${enc.phone_plaintext ?? '—'} / ${enc.address_plaintext ?? '—'} / ${enc.location_plaintext ?? '—'} • emails encrypted/plaintext/hash: ${emailEnc.email_encrypted ?? '—'} / ${emailEnc.email_plaintext ?? '—'} / ${emailEnc.email_hash_present ?? '—'} • old raw session rows: ${retention.auth_sessions_raw_old ?? '—'}`));
          if (backups.length){
            metaInfo.appendChild(mutedNode(`latest security backup: ${backups[0].filename || '—'} (${backups[0].row_count ?? '—'} users • ${backups[0].encrypted ? 'encrypted' : 'plaintext legacy'})`));
          }
          if (j.all_room_e2ee_impact && j.all_room_e2ee_impact.summary){
            metaInfo.appendChild(mutedNode(`all-room E2EE impact: ${j.all_room_e2ee_impact.summary}`));
          }
          const checklist = j.security_setup_checklist || {};
          if (checklist && Object.keys(checklist).length){
            metaInfo.appendChild(mutedNode(`finish setup checklist: ${checklist.ready ? 'ready' : 'needs attention'} • profile plaintext left=${checklist.profile_plaintext_fields_remaining ?? '—'} • email plaintext/hash missing=${checklist.email_plaintext_or_hash_missing_remaining ?? '—'} • latest backup encrypted=${checklist.latest_backup_encrypted ? 'yes' : 'no'}`));
          }
          if (j.profile_migration_run){
            const run = j.profile_migration_run;
            metaInfo.appendChild(mutedNode(`last security operation: ${run.mode || '—'} updated_users=${run.updated_users ?? 0} updated_fields=${run.updated_fields ?? 0} undecryptable=${run.undecryptable_fields ?? 0}`));
            if (Array.isArray(run.steps) && run.steps.length){
              const stepList = el('div');
              stepList.style.marginTop = '6px';
              run.steps.forEach(step => {
                const res = step.result || {};
                const line = `${step.label || step.key || 'step'}: ${res.ok === false ? 'failed' : 'ok'}${res.error ? ` — ${res.error}` : ''}${res.updated_users != null ? ` • users=${res.updated_users}` : ''}${res.updated_fields != null ? ` • fields=${res.updated_fields}` : ''}${res.row_count != null ? ` • backup rows=${res.row_count}` : ''}`;
                stepList.appendChild(mutedNode(line));
              });
              metaInfo.appendChild(stepList);
            }
          }
          meta.appendChild(metaInfo);
          list.appendChild(meta);
        }
      }
    }

    async function refreshSecurityStatus(){
      const j = await getJSON('/admin/security/status');
      renderSecurityStatus(j);
    }

    async function runPrivacyRetentionNow(){
      const r = await adminFetch('/admin/security/status', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'privacy_retention'})});
      const j = await r.json().catch(()=>null);
      if (!r.ok || !j || j.ok === false){
        toast('err', 'Privacy retention failed', (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`, 5200);
        return;
      }
      const upd = (j.retention_run && j.retention_run.updated) || {};
      toast('ok', 'Privacy retention complete', `sessions=${upd.auth_sessions || 0} tokens=${upd.auth_tokens || 0} reset=${upd.password_reset_tokens || 0} audit=${upd.audit_log || 0}`);
      renderSecurityStatus(j);
    }

    async function runProfileSecurityAction(action, title){
      if (action === 'rotate_profile_field_key'){
        const ok = await adminConfirm('Rotate profile field encryption', 'Set ECHOCHAT_PROFILE_FIELD_KEY to the new key and ECHOCHAT_PROFILE_FIELD_PREVIOUS_KEYS to old key(s) before running. A security backup will be created first.', {danger:true, confirmText:'Rotate fields'});
        if (!ok) return;
      }
      if (action === 'encrypt_plaintext_profile_fields' || action === 'encrypt_plaintext_emails'){
        const ok = await adminConfirm('Encrypt stored user data', 'EchoChat will create a security backup first, then rewrite legacy plaintext fields in place.', {danger:true, confirmText:'Create backup and encrypt'});
        if (!ok) return;
      }
      if (action === 'restore_latest_security_backup'){
        const ok = await adminConfirm('Restore latest security backup', 'This rewrites email/phone/address/location fields from the most recent backup. Use only to roll back a failed encryption or key-rotation action.', {danger:true, confirmText:'Restore latest backup'});
        if (!ok) return;
      }
      if (action === 'finish_security_setup'){
        const ok = await adminConfirm('Finish Security Setup', 'EchoChat will create an encrypted security backup, encrypt old phone/address/location rows, encrypt old email rows, and run privacy retention. Make sure your encryption keys are backed up first.', {danger:true, confirmText:'Create backup and finish setup'});
        if (!ok) return;
      }
      const payload = {action};
      if (action === 'finish_security_setup') payload.limit = 100000;
      const r = await adminFetch('/admin/security/status', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const j = await r.json().catch(()=>null);
      if (!r.ok || !j || j.ok === false || (j.profile_migration_run && j.profile_migration_run.ok === false)){
        const msg = (j && j.profile_migration_run && (j.profile_migration_run.error || j.profile_migration_run.message)) || (j && (j.error || j.message)) || `HTTP ${r.status}`;
        toast('err', `${title} failed`, msg, 7000);
        renderSecurityStatus(j);
        return;
      }
      const run = j.profile_migration_run || {};
      const stepCount = Array.isArray(run.steps) ? ` steps=${run.steps.length}` : '';
      toast('ok', title, `updated_users=${run.updated_users || 0} updated_fields=${run.updated_fields || 0}${stepCount}`);
      renderSecurityStatus(j);
    }

    async function refreshDiagnostics(){
      const overall = document.getElementById('ecapDiagOverall');
      const when = document.getElementById('ecapDiagWhen');
      const schema = document.getElementById('ecapDiagSchema');
      const list = document.getElementById('ecapDiagChecks');
      const j = await getJSON('/admin/diagnostics');
      if (!j || j.ok === false){
        if (overall){ overall.textContent = 'overall: unavailable'; overall.className = 'ecap-pill bad'; }
        if (when){ when.textContent = 'checked: failed'; when.className = 'ecap-pill warn'; }
        if (schema){ schema.textContent = 'schema: —'; schema.className = 'ecap-pill'; }
        if (list) setListStatus(list, 'Diagnostics endpoint unavailable.');
        return;
      }
      const cur = j.current || {};
      const counts = cur.counts || {};
      const state = String(cur.overall || 'unknown');
      if (overall){
        overall.textContent = `overall: ${state} (ok ${counts.ok||0} • warn ${counts.warn||0} • fail ${counts.fail||0})`;
        overall.className = `ecap-pill ${state === 'fail' ? 'bad' : state === 'warn' ? 'warn' : 'ok'}`;
      }
      if (when){
        when.textContent = `checked: ${cur.timestamp || '—'}`;
        when.className = 'ecap-pill warn';
      }
      if (schema){
        schema.textContent = `schema: ${j.schema_state || '—'}`;
        schema.className = 'ecap-pill';
      }
      if (list){
        clearNode(list);
        const checks = Array.isArray(cur.checks) ? cur.checks : [];
        if (!checks.length){
          list.appendChild(listStatusNode('No diagnostics checks returned.'));
        } else {
          checks.forEach(ch => {
            const row = el('div', {class:'ecap-item'});
            const st = String(ch.status || 'info');
            const cls = st === 'fail' ? 'bad' : st === 'warn' ? 'warn' : st === 'ok' ? 'ok' : '';
            const info = el('div');
            info.style.minWidth = '0';
            const title = el('div', {text:ch.name || 'check'});
            title.style.fontWeight = '750';
            title.style.whiteSpace = 'nowrap';
            title.style.overflow = 'hidden';
            title.style.textOverflow = 'ellipsis';
            const badge = pillNode(st, cls);
            badge.style.marginLeft = '6px';
            title.appendChild(document.createTextNode(' '));
            title.appendChild(badge);
            const summary = mutedNode(ch.summary || '');
            summary.style.marginTop = '4px';
            info.appendChild(title);
            info.appendChild(summary);
            row.appendChild(info);
            list.appendChild(row);
          });
        }
      }
    }

    btnRefresh.addEventListener('click', ()=> withAdminAction(btnRefresh, 'admin:refresh-all', 'Refreshing', async ()=>{
      await Promise.allSettled([
        refreshStats(),
        refreshModeration(),
        refreshRooms(),
        refreshIncidentMode(),
        refreshRolesUI(),
        refreshAudit(),
        runSearch(),
        refreshVoiceSettings(),
        refreshIceSettings(),
        refreshMediaStatus(),
        refreshDiagnostics(),
        refreshAnalytics()
      ]);
      toast('info','Refreshed','Stats + lists updated');
      log('manual refresh');
    }));

    const voiceSettingsReloadBtn = secVoice.querySelector('#ecapVoiceSettingsReload');
    if (voiceSettingsReloadBtn) voiceSettingsReloadBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'voice:reload', 'Loading', refreshVoiceSettings));
    const voiceSettingsApplyBtn = secVoice.querySelector('#ecapVoiceSettingsApply');
    if (voiceSettingsApplyBtn) voiceSettingsApplyBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'voice:apply', 'Saving', ()=>applyVoiceSettings('voice')));
    const iceReloadBtn = secVoice.querySelector('#ecapIceReload');
    if (iceReloadBtn) iceReloadBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'ice:reload', 'Loading', refreshIceSettings));
    const iceApplyBtn = secVoice.querySelector('#ecapIceApply');
    if (iceApplyBtn) iceApplyBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'ice:apply', 'Saving', applyIceSettings));

    const mediaRefreshBtn = secAv.querySelector('#ecapMediaRefresh');
    if (mediaRefreshBtn) mediaRefreshBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'media:refresh', 'Loading', refreshMediaStatus));
    const webcamEnabledToggle = secAv.querySelector('#ecapWebcamEnabled');
    if (webcamEnabledToggle) webcamEnabledToggle.addEventListener('change', () => {
      const modeSel = secAv.querySelector('#ecapAvModeSelect');
      if (webcamEnabledToggle.checked && modeSel && modeSel.value !== 'echo') modeSel.value = 'echo';
    });
    const mediaApplyBtn = secAv.querySelector('#ecapMediaApply');
    if (mediaApplyBtn) mediaApplyBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'media:apply', 'Saving', applyMediaSettings));

    const testLabBtn = secDash.querySelector('#ecapOpenTestLab');
    if (testLabBtn) testLabBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'test-lab:open', 'Opening', openAdminTestLab));
    const securityRefreshBtn = secDash.querySelector('#ecapSecurityRefresh');
    if (securityRefreshBtn) securityRefreshBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:refresh', 'Loading', refreshSecurityStatus));
    const securityFinishSetupBtn = secDash.querySelector('#ecapSecurityFinishSetup');
    if (securityFinishSetupBtn) securityFinishSetupBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:finish', 'Running', ()=>runProfileSecurityAction('finish_security_setup', 'Security setup finished')));
    const securityRetentionBtn = secDash.querySelector('#ecapSecurityRunRetention');
    if (securityRetentionBtn) securityRetentionBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:retention', 'Running', runPrivacyRetentionNow));
    const securityEncryptProfilesBtn = secDash.querySelector('#ecapSecurityEncryptProfiles');
    if (securityEncryptProfilesBtn) securityEncryptProfilesBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:encrypt-profiles', 'Encrypting', ()=>runProfileSecurityAction('encrypt_plaintext_profile_fields', 'Profile field encryption complete')));
    const securityEncryptEmailsBtn = secDash.querySelector('#ecapSecurityEncryptEmails');
    if (securityEncryptEmailsBtn) securityEncryptEmailsBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:encrypt-emails', 'Encrypting', ()=>runProfileSecurityAction('encrypt_plaintext_emails', 'Email encryption complete')));
    const securityRotateProfilesBtn = secDash.querySelector('#ecapSecurityRotateProfiles');
    if (securityRotateProfilesBtn) securityRotateProfilesBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:rotate-profiles', 'Rotating', ()=>runProfileSecurityAction('rotate_profile_field_key', 'Profile field rotation complete')));
    const securityBackupBtn = secDash.querySelector('#ecapSecurityBackup');
    if (securityBackupBtn) securityBackupBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:backup', 'Backing up', ()=>runProfileSecurityAction('create_security_backup', 'Security backup created')));
    const securityRestoreBackupBtn = secDash.querySelector('#ecapSecurityRestoreBackup');
    if (securityRestoreBackupBtn) securityRestoreBackupBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'security:restore-backup', 'Restoring', ()=>runProfileSecurityAction('restore_latest_security_backup', 'Security backup restored')));
    const diagRefreshBtn = secDash.querySelector('#ecapDiagRefresh');
    if (diagRefreshBtn) diagRefreshBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'diagnostics:refresh', 'Running', refreshDiagnostics));
    const analyticsRefreshBtn = secDash.querySelector('#ecapAnalyticsRefresh');
    if (analyticsRefreshBtn) analyticsRefreshBtn.addEventListener('click', (e)=> withAdminAction(e.currentTarget, 'analytics:refresh', 'Loading', refreshAnalytics));

    // keep Users tab in sync if dashboard input changes.  This listener is safe
    // before unlock because it only mirrors local input; it does not fetch data
    // unless a user value is entered after the panel unlocks.
    const dashTargetInput = secDash.querySelector('#ecapTargetInput');
    dashTargetInput.addEventListener('input', ()=>{
      if (!adminStartupUnlocked) return;
      const u = (dashTargetInput.value||'').trim();
      if (u) setTargetUser(u, {syncInput:false, loadDetail:false});
    });

    Object.assign(adminRuntimeFns, {
      refreshVoiceSettings,
      refreshIceSettings,
      refreshMediaStatus,
      refreshStats,
      refreshSecurityStatus,
      refreshDiagnostics,
      refreshAnalytics,
      runSearch
    });

    log('admin panel shell injected; waiting for startup password confirmation');
  }

  function adminRefreshWhenVisible(fn){
    return ()=>{
      if (document.hidden || !adminStartupUnlocked) return;
      try { fn(); } catch(_e) {}
    };
  }

  function startAdminPanelRuntime(){
    if (!adminStartupUnlocked || adminRuntimeStarted) return;
    adminRuntimeStarted = true;
    setAdminStartupLocked(false);
    adminRuntimeFns.refreshVoiceSettings();
    adminRuntimeFns.refreshIceSettings();
    adminRuntimeFns.refreshMediaStatus();
    adminRuntimeFns.refreshStats();
    adminRuntimeFns.refreshSecurityStatus();
    adminRuntimeFns.refreshDiagnostics();
    adminRuntimeFns.refreshAnalytics();
    if (!adminIntervalsStarted){
      adminIntervalsStarted = true;
      setInterval(adminRefreshWhenVisible(adminRuntimeFns.refreshStats), 60000);
      setInterval(adminRefreshWhenVisible(adminRuntimeFns.refreshMediaStatus), 120000);
      setInterval(adminRefreshWhenVisible(adminRuntimeFns.refreshSecurityStatus), 120000);
      setInterval(adminRefreshWhenVisible(adminRuntimeFns.refreshDiagnostics), 120000);
      setInterval(adminRefreshWhenVisible(adminRuntimeFns.refreshAnalytics), 120000);
    }
    adminRuntimeFns.runSearch();
    log('admin panel unlocked; live admin data loaded');
    toast('ok','Admin panel unlocked', 'Live controls and data are now available');
  }

  async function requestAdminPanelStartupUnlock(){
    if (adminStartupUnlocked){
      startAdminPanelRuntime();
      return true;
    }
    if (adminStartupUnlockPromise) return adminStartupUnlockPromise;
    adminStartupUnlockPromise = (async ()=>{
      const panel = showPanel({unmini:true});
      if (!panel) return false;
      setAdminStartupLocked(true);

      // If the server says this login session is already freshly confirmed, do
      // not ask again.  Otherwise, no admin data is loaded until the password
      // dialog succeeds.
      let alreadyFresh = false;
      try{ alreadyFresh = await ensureAdminReauthAlreadyFresh(null); }catch(_){ alreadyFresh = false; }
      if (!alreadyFresh){
        const ok = await confirmAdminPassword('Confirm your password before the admin panel loads.');
        if (!ok){
          setAdminStartupLocked(false);
          hidePanel();
          log('admin panel startup unlock canceled; panel hidden and live data was not loaded');
          return false;
        }
      }

      adminStartupUnlocked = true;
      startAdminPanelRuntime();
      return true;
    })();
    try{
      return await adminStartupUnlockPromise;
    }finally{
      adminStartupUnlockPromise = null;
    }
  }

  function boot(){
    try{
      buildPanel();
      if (!state.closed) requestAdminPanelStartupUnlock();
    }catch(e){ console.error(e); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

"""

    password_policy = password_policy_metadata()
    js = (
        js.replace("__ECHOCHAT_USERNAME_MIN__", str(USERNAME_MIN_LENGTH))
        .replace("__ECHOCHAT_USERNAME_MAX__", str(USERNAME_MAX_LENGTH))
        .replace("__ECHOCHAT_USERNAME_PATTERN__", json.dumps(USERNAME_HTML_PATTERN))
        .replace("__ECHOCHAT_USERNAME_TITLE__", json.dumps(username_policy_title()))
        .replace("__ECHOCHAT_PASSWORD_MIN__", str(password_policy.get("min_length")))
        .replace("__ECHOCHAT_PASSWORD_MAX__", str(password_policy.get("max_length")))
        .replace("__ECHOCHAT_PASSWORD_RECOMMENDED__", str(password_policy.get("recommended_length")))
        .replace("__ECHOCHAT_PASSWORD_SUMMARY__", json.dumps(password_policy.get("summary")))
        .replace("__ECHOCHAT_PASSWORD_COMMON_WEAK__", json.dumps(password_policy.get("common_weak") or []))
    )

    nonce_attr = f' nonce="{escape(csp_nonce, quote=True)}"' if csp_nonce else ""

    snippet = (
        "\n<!-- Admin Panel (server-injected; admin-only) -->\n"
        f"<style id=\"ecAdminCss\">{css}</style>\n"
        f"<script id=\"ecAdminJs\"{nonce_attr}>{js}</script>\n"
    )
    return snippet

def inject_admin_panel(html: str, csp_nonce: str | None = None) -> str:
    """Inject the admin panel snippet into the provided HTML document."""
    snippet = build_admin_injection_snippet(csp_nonce=csp_nonce)
    lower = html.lower()
    marker = "</head>"
    idx = lower.rfind(marker)
    if idx == -1:
        return html + snippet
    return html[:idx] + snippet + html[idx:]
