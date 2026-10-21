'use strict';
const $=sel=>document.querySelector(sel);
const ico=n=>`<svg class="ic" aria-hidden="true"><use href="#i-${n}"></use></svg>`;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>new Intl.NumberFormat('ru-RU').format(Number(n)||0);
const safeLink=u=>{try{const url=new URL(u);return ['https:','http:'].includes(url.protocol)?url.href:'';}catch{return '';}};
const date=s=>{try{return s?new Date(s).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Дата неизвестна';}catch{return 'Дата неизвестна';}};
const viewNames={dashboard:'Обзор',public:'Public Hunter',company:'Company Hunter',leads:'База лидов',ad:'Добавить рекламу',map:'Map Hunter',partner:'Партнёры',request:'Прямые запросы',pipeline:'Воронка',settings:'Настройки'};
const types={directory:'Бизнес-каталоги / VK',ad:'Местная реклама',map:'Карты / OSM',partner:'Партнёр',request:'Прямой запрос',manual:'Вручную'};
const statuses={new:'Новый',research:'Проверка',ready:'Готов к контакту',contacted:'Связался',replied:'Ответили',won:'Оплачен',lost:'Отказ',skip:'Не подходит'};
const webStatuses={unknown:'Не проверен',not_found:'Не обнаружен',found:'Есть сайт'};
const kinds={vk:'VK API',rss:'RSS',manual:'Telegram / вручную'};
const freshLabels={hot:'Горячее · 0–2 ч',fresh:'Свежая · до 24 ч',archive:'Архив · старше суток',undated:'Дата не указана'};
let state={view:'dashboard',leads:[],sources:[],posts:[],freshPosts:[],stats:{},publicStats:{},config:{},selectedId:null,detail:null,filter:'all',sort:'score',search:'',postStatus:'fresh',postSource:'all',targetOnly:true,targetNiche:'all',targetSettingsOpen:false,sourceSearch:'',reviewId:null,companyEntries:[],companyJob:{state:'idle'},companyNiche:'all',companySelected:null,companyGeoCity:'Рязань',discovery:{state:'idle'},vkDiscovery:{state:'idle'},companyVkCity:'Рязань',companyVkNiche:'furniture' };
async function api(url,method='GET',data){const options={method,headers:{'Content-Type':'application/json'}};if(data!==undefined)options.body=JSON.stringify(data);const r=await fetch(url,options);let body;try{body=await r.json();}catch{throw Error('Сервер вернул неверный ответ');}if(!r.ok)throw Error(body.error||`Ошибка ${r.status}`);return body;}
function toast(msg,bad=false){const n=document.createElement('div');n.className='toast'+(bad?' error':'');n.textContent=msg;$('#toasts').append(n);setTimeout(()=>n.remove(),5500);}
function elink(url,label){const safe=safeLink(url);return safe?`<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label||safe)} ↗</a>`:'<span class="muted">Нет</span>';}
function options(o,selected){return Object.entries(o).map(([v,k])=>`<option value="${esc(v)}" ${v===selected?'selected':''}>${esc(k)}</option>`).join('');}
function pageTitle(tag,headline,sub,buttons=''){return `<div class="page-title"><div><div class="eyebrow">${esc(tag)}</div><h1>${esc(headline)}</h1><p>${esc(sub)}</p></div><div class="action-group">${buttons}</div></div>`;}
function noData(head,sub){return `<div class="empty">${ico('search')}<strong>${esc(head)}</strong><p>${esc(sub)}</p></div>`;}
function actualLeads(){return state.leads.filter(l=>!l.demo);}
function getFiltered(){const f=state.search.toLowerCase().trim();return state.leads.filter(l=>{
 if(state.filter!=='all'&&l.status!==state.filter)return false;
 if(['ad','map','partner','request'].includes(state.view)&&l.source_type!==state.view)return false;
 if(f&&!`${l.name} ${l.city} ${l.niche} ${l.description} ${l.source_name}`.toLowerCase().includes(f))return false;
 return true;
 }).sort((a,b)=>a.demo!==b.demo?(a.demo?1:-1):state.sort==='recent'?b.id-a.id:state.sort==='value'?b.potential_value-a.potential_value:b.score-a.score);}
async function load(){const [leads,stats,sources,posts,freshPosts,publicStats]=await Promise.all([api('/api/leads'),api('/api/stats'),api('/api/public/sources'),api('/api/public/posts?status='+state.postStatus),api('/api/public/posts?status=fresh'),api('/api/public/stats')]);Object.assign(state,{leads,stats,sources,posts,freshPosts,publicStats});if(state.selectedId&&leads.some(l=>l.id===state.selectedId)){state.detail=await api('/api/leads/'+state.selectedId);}else{state.detail=null;}$('#navCount').textContent=stats.total;$('#publicCount').textContent=publicStats.review;render();}
function route(page){state.view=page;if(page==='company')refreshCompany().catch(e=>toast(e.message,true));state.selectedId=null;state.detail=null;state.filter='all';state.search='';$('#search').value='';$('#sidebar').classList.remove('open');render();}
function render(){document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===state.view));$('#currentPage').textContent=viewNames[state.view]||'VOID LEADS';let html='';if(state.view==='dashboard')html=dashboard();else if(state.view==='public')html=publicHunter();else if(state.view==='company')html=companyHunter();else if(['leads','ad','map','partner','request'].includes(state.view))html=workspace();else if(state.view==='pipeline')html=pipeline();else html=settings();$('#content').innerHTML=html;}
function metric(label,value,iconName,foot,accent=false){return `<div class="metric glass"><div class="metric-top"><span>${esc(label)}</span>${ico(iconName)}</div><div class="metric-number ${accent?'accent-number':''}">${esc(value)}</div><div class="metric-foot">${esc(foot)}</div></div>`;}
function dashboard(){const s=state.stats,p=state.publicStats;const newest=state.freshPosts.slice(0,5);return pageTitle('РАБОЧЕЕ ПРОСТРАНСТВО','Данил, свежие возможности — сегодня.','Ищем компании в городах и нишах, проверяем сайты и выводим кандидатов для личного обращения. Реклама и прямые запросы остаются отдельным источником.',`<button class="btn soft" data-view="public">${ico('megaphone')} Public Hunter</button><button class="btn primary" data-view="company">${ico('search')} Company Hunter</button>`)
 +`<div class="metric-grid">${metric('Моих пабликов',p.sources||0,'megaphone','Разные города и источники')}${metric('Перспективных за 24 ч',p.target_fresh||0,'bolt','Свежие компании в целевых нишах',true)}${metric('За последние 2 ч',p.hot||0,'activity','Самые горячие находки')}${metric('Оплачено',fmt(s.revenue||0)+' ₽','leads','По реальным закрытым сделкам')}</div>`
 +`<div class="content-grid"><section class="panel glass"><div class="panel-header"><h2>Самые свежие рекламодатели</h2><button class="btn quiet small" data-view="public">Открыть ленту ${ico('arrow')}</button></div>${newest.length?newest.map(p=>`<div class="tile"><div class="tile-header"><div><h3>${esc(p.suggested_name)}</h3><div class="tile-meta">${esc(p.city)} · ${date(p.published_at)} · ${esc(p.source_name)}</div></div><span class="chip ${p.freshness==='hot'?'blue':''}">${esc(freshLabels[p.freshness])}</span></div><p>${esc(p.text.slice(0,200))}</p><div class="tile-bottom">${p.source_url?elink(p.source_url,'Первоначальный пост'):'<span class="chip">Без ссылки</span>'}<button class="btn soft small" data-view="public">В Public Hunter ${ico('arrow')}</button></div></div>`).join(''):noData('За 24 часа подходящей рекламы нет','Подключи паблики через CSV и проверь источники. Старые посты не считаются новыми.')}</section><section class="panel glass blue-glow"><div class="eyebrow">ПЛАН НА СЕГОДНЯ</div><h2 style="margin:10px 0 0;font-size:24px;letter-spacing:-.8px">Ищем компании, которым можем предложить результат</h2><p class="pitch-text">Начни с Company Hunter: поиск бизнес-сообществ VK, Wikidata, OpenStreetMap и последующая проверка сайтов. Каждое предложение подтверждаем реальными наблюдениями.</p><div class="tip-list"><div class="tip-item">${ico('check')} Поиск компаний по нишам и городам</div><div class="tip-item">${ico('check')} Аудит сайтов с объяснением признаков</div><div class="tip-item">${ico('check')} Сохранение переписок и статусов сделок</div><div class="tip-item">${ico('check')} Никаких автоматических рассылок</div></div><div class="actions" style="margin-top:25px"><button class="btn primary" data-view="company">Перейти к поиску ${ico('arrow')}</button></div></section></div>`;}

function leadTile(l){return `<div class="tile"><div class="tile-header"><div><h3>${esc(l.name)}${l.demo?' <span class="chip demo">ДЕМО</span>':''}</h3><div class="tile-meta">${esc(l.city||'Город не указан')} · ${esc(types[l.source_type]||'Вручную')}</div></div><span class="score">${l.score}/100</span></div><p>${esc((l.description||'Описание пока не добавлено').slice(0,170))}</p><div class="tile-bottom"><span class="chip ${l.ad_count>1?'blue':''}">${l.ad_count>1?`Реклама × ${l.ad_count}`:webStatuses[l.website_status]}</span><button class="btn soft small" data-view="leads" data-select="${l.id}">Открыть ${ico('arrow')}</button></div></div>`;}
function sourceTile(s){return `<article class="source-card" ${!s.enabled?'style="opacity:.62"':''}><div class="source-card-top"><div><h3>${esc(s.name)}</h3><p>${esc(s.city)} · ${esc(kinds[s.kind])}</p></div><span class="chip ${s.new_posts?'blue':''}">${s.new_posts||0} новых</span></div><p>${s.kind==='manual'&&s.locator?elink(s.locator,'Открыть паблик'):s.locator?esc(s.locator):'Публикации добавляются вручную'} · ${s.last_checked?'Проверен '+date(s.last_checked):'Ещё не проверялся'} · ${s.interval_minutes||20} мин</p>${s.last_checked?`<div class="source-diagnostics"><span>Прочитано <b>${s.last_count||0}</b></span><span>Реклама <b>${s.last_matched||0}</b></span><span>Отсеяно <b>${s.last_filtered||0}</b></span><span>В очереди <b>${s.last_reviewable||0}</b></span><span>В архиве <b>${s.last_archive||0}</b></span><span>Новых <b>${s.last_added||0}</b></span></div>`:''}<p class="source-yield"><b>${s.targets_7d||0} перспективных</b> из ${s.ads_7d||0} рекламных публикаций за 7 дней</p>${s.last_checked&&s.last_count>=20&&s.last_matched===0?'<p class="hint source-warning">За последнюю проверку подходящей рекламы нет. Возможно, этот источник стоит заменить.</p>':''}${s.last_error?`<p class="hint error-hint">Ошибка: ${esc(s.last_error)}</p>`:''}<div class="actions"><button class="btn quiet small" data-action="toggle-source" data-id="${s.id}">${s.enabled?'Пауза':'Включить'}</button>${s.kind==='manual'?`<button class="btn soft small" data-action="open-post" data-source="${s.id}">Добавить пост</button>`:`<button class="btn soft small" data-action="check-source" data-id="${s.id}">${ico('refresh')} Проверить</button>`}${s.last_reviewable?`<button class="btn soft small" data-action="review-source" data-id="${s.id}">Разобрать ${s.last_reviewable}</button>`:''}<button class="btn quiet small" data-action="remove-source" data-id="${s.id}">${ico('trash')}</button></div></article>`;}
function postTile(p){return `<article class="post"><div class="post-head"><div><strong>${esc(p.source_name)}</strong><small>${esc(p.city)} · ${date(p.published_at)} · ${esc(kinds[p.source_kind]||'Ручной')}</small></div><span class="chip ${p.freshness==='hot'?'blue':''}">${esc(freshLabels[p.freshness]||'Неизвестно')}</span>${p.promoted?'<span class="chip blue">Помечена как реклама</span>':''}<div class="two-scores"><span class="score muted-score" title="Распознавание рекламы">Реклама ${p.score}/100</span><span class="score ${qualifiesTarget(p)?'target-strong':'target-muted'}" title="Потенциал как клиента">Клиент ${p.target_score||0}/100</span></div></div><div class="post-text" style="margin-top:13px">${esc(p.text)}</div>${p.analysis_reason?`<p class="post-explanation"><b>${p.review_status==='filtered'?'Почему отсеяно:':'Почему в ленте:'}</b> ${esc(p.analysis_reason)}</p>`:''}<p class="post-explanation"><b>Потенциал:</b> ${esc(p.target_reason||'Нужна проверка бизнеса')}. <b>Сайт:</b> ${p.site_evidence==='linked'?'Ссылка есть, принадлежность не подтверждена':'Не проверен — отсутствие ссылки не означает отсутствие сайта'}</p><div class="post-footer"><span class="chip blue">${esc(state.config.niches?.[p.niche]||'Не определено')}</span>${p.source_url?elink(p.source_url,'Исходный пост'):'<span class="chip">Без ссылки</span>'}${p.review_status==='filtered'?`<button class="btn soft small" data-action="restore" data-id="${p.id}">Это реклама · вернуть</button>`:p.review_status==='new'?`<button class="btn primary small" data-action="convert" data-id="${p.id}">В CRM ${ico('arrow')}</button><button class="btn quiet small" data-action="ignore" data-id="${p.id}">Пропустить</button>`:`<span class="chip green">${p.review_status==='converted'?'В базе':'Пропущено'}</span>`}</div></article>`;}
function publicHunter(){const p=state.publicStats;return pageTitle('ПОИСК / 01','Public Hunter','Только коммерческие предложения за последние 24 часа. Новости и сомнительные публикации — отдельно, с возможностью ручной проверки.',`<button class="btn soft" data-action="import-sources">${ico('upload')} Импорт пабликов CSV</button><button class="btn soft" data-action="open-post">${ico('plus')} Добавить пост</button><button class="btn primary" data-action="open-source">${ico('plus')} Новый источник</button>`)
 +`<div class="notes-grid"><div class="note-kpi glass"><strong>${p.sources||0}</strong><span>Подключено источников</span></div><div class="note-kpi glass"><strong style="color:var(--blue)">${p.hot||0}</strong><span>Горячих · за 2 часа</span></div><div class="note-kpi glass"><strong>${p.fresh||0}</strong><span>Свежих · за 24 часа</span></div></div>`
 +`<div class="tool-box glass fresh-banner"><div>${ico('clock')} <strong>Повторная проверка через 60 секунд</strong><p>Автоматически, пока программа работает. VK требует разрешённый токен; Telegram — ручной импорт. В фоновом режиме опрашиваем источники по очереди; при большом числе пабликов интервал может увеличиваться. Старые посты — в архиве.</p></div><div class="fresh-controls"><span class="chip">В архиве: ${p.archive||0}</span><span class="chip">Без даты: ${p.undated||0}</span><span class="chip">Сохранено для проверки: ${p.filtered||0}</span><span class="chip blue">Перспективных: ${p.target_fresh||0}</span></div></div>`
 +`<div class="source-grid"><section class="panel glass"><div class="panel-header"><h2>Мои паблики и источники</h2><div class="action-group"><span class="chip">${state.sources.length} шт.</span><button class="btn soft small" data-action="check-all" ${state.sources.some(s=>s.kind!=='manual'&&s.enabled)?'':'disabled'}>${ico('refresh')} Проверить все</button></div></div>
 <div class="source-tools"><input class="control" id="sourceSearch" placeholder="Поиск по названию или городу" value="${esc(state.sourceSearch||'')}"><div class="actions"><a class="btn quiet small" href="/api/public/sources/template.csv">${ico('file')} Шаблон CSV</a><button class="btn soft small" data-action="import-sources">${ico('upload')} Импорт CSV</button></div></div>
 <div class="source-card-list" id="sourceCards">${sourceCards()}</div>
 <div class="divide"></div><h3 style="font-size:18px;margin:0 0 9px">VK API — сервисный ключ</h3><p class="hint" style="font-size:15px;line-height:1.6">Для чтения постов чужих открытых пабликов нужен <strong>сервисный ключ твоего приложения VK</strong> — не ключ сообщества и не «защищённый ключ». <a href="https://dev.vk.com/" target="_blank" rel="noopener noreferrer">Открыть VK для разработчиков ↗</a></p><input type="password" class="control" id="vkToken" placeholder="${state.config.vk_token_saved?'VK-токен сохранён в Windows':'Вставь сервисный ключ VK'}" autocomplete="new-password" aria-label="Сервисный ключ доступа VK"><div class="actions" style="margin-top:9px"><button class="btn primary small" data-action="save-vk-token">Сохранить ключ на этом ПК</button><button class="btn quiet small" data-action="delete-vk-token">Удалить ключ</button></div><p class="hint">Токен сохраняется только по твоему нажатию и шифруется Windows DPAPI под твоей учётной записью. Он не попадает в SQLite, CSV или GitHub. Состояние: ${state.config.vk_token_saved?'сохранён · фоновая проверка доступна':'не сохранён'}.</p></section>
 <section class="panel glass"><div class="panel-header public-posts-heading"><h2>Рекламные публикации</h2><span class="chip">${visiblePosts().length} в выборке</span></div>
<div class="public-filter-panel" aria-label="Фильтры рекламных публикаций">
 <div class="public-filter-grid">
 <label class="public-filter-field"><span>Источник</span><select class="control" id="postSource" aria-label="Фильтр по паблику"><option value="all">Все источники</option>${state.sources.map(s=>`<option value="${s.id}" ${String(state.postSource)===String(s.id)?'selected':''}>${esc(s.name)} · ${esc(s.city)}</option>`).join('')}</select></label>
 <label class="public-filter-field"><span>Публикации</span><select class="control" id="postStatus"><option value="fresh" ${state.postStatus==='fresh'?'selected':''}>Свежие · 24 часа</option><option value="archive" ${state.postStatus==='archive'?'selected':''}>Архив · 24+ часа</option><option value="undated" ${state.postStatus==='undated'?'selected':''}>Без даты</option><option value="filtered" ${state.postStatus==='filtered'?'selected':''}>Отсеяно фильтром</option><option value="converted" ${state.postStatus==='converted'?'selected':''}>Добавлены в CRM</option><option value="all" ${state.postStatus==='all'?'selected':''}>Всё за историю</option></select></label>
 <label class="public-filter-field"><span>Отрасль</span><select class="control" id="targetNiche"><option value="all">Все ниши</option>${Object.entries(state.config.target_niche_names||{}).map(([k,v])=>`<option value="${esc(k)}" ${state.targetNiche===k?'selected':''}>${esc(v)}</option>`).join('')}</select></label>
 <label class="public-filter-toggle" for="targetOnly"><span class="public-toggle-copy"><strong>Только перспективные</strong><small>Высокий потенциал клиента</small></span><input type="checkbox" id="targetOnly" role="switch" aria-label="Показывать только перспективные публикации" ${state.targetOnly?'checked':''}><span class="public-toggle-track" aria-hidden="true"><span></span></span></label>
 </div></div>${targetSettings()}<div class="posts-list">${state.postStatus==='filtered'?'<p class="hint">Здесь показаны отклонённые посты любого возраста, найденные за последние 7 дней. Для новых источников нужно выполнить проверку после обновления.</p>':''}${visiblePosts().length?visiblePosts().map(postTile).join(''):noData(state.postStatus==='fresh'?'Новых публикаций пока нет':'В этой категории пока пусто',state.postStatus==='filtered'?'Проверь источник повторно. Ранее отброшенные старые посты не сохранялись в v0.6.0.':'Проверь источники или добавь свежий пост с датой публикации.')}</div></section></div>`;}
function qualifiesTarget(p){
 const prefs=state.config.preferences||{};
 return p.review_status!=='filtered' &&
   (prefs.target_niches||[]).includes(p.niche) &&
   Number(p.target_score||0)>=Number(prefs.min_notification_score??60);
}
function showUpdateOverlay(){
 let box=$('#updateOverlay');
 if(box)return box;
 box=document.createElement('div');
 box.id='updateOverlay';
 box.className='update-overlay';
 box.setAttribute('role','status');
 box.setAttribute('aria-live','polite');
 box.innerHTML='<div class="update-overlay-card"><span class="update-overlay-icon">V</span><strong>VOID LEADS обновляется</strong><p id="updateStage">Подготавливаем безопасное обновление…</p><div class="update-progress-track"><span></span></div><small>Не выключай компьютер до завершения. Новая версия откроется автоматически.</small></div>';
 document.body.append(box);
 return box;
}
function monitorUpdate(){
 showUpdateOverlay();
 let misses=0;
 const timer=setInterval(async()=>{
  try{
   const data=await api('/api/update/progress');
   misses=0;
   const stage=$('#updateStage');
   if(stage)stage.textContent=data.message||'Обновление выполняется…';
   if(data.state==='failed'||data.state==='idle'){
    clearInterval(timer);
    document.querySelector('#updateOverlay')?.remove();
    toast(data.message||'Не удалось выполнить обновление',true);
    const btn=$('#installUpdateButton');
    if(btn)btn.disabled=false;
   }
   if(data.state==='closing'){
    if(stage)stage.textContent='Старая версия закрывается. Новая программа откроется сама.';
    clearInterval(timer);
    setTimeout(()=>{try{window.close();}catch{}},850);
   }
  }catch{
   misses++;
   if(misses>=3){
    clearInterval(timer);
    const stage=$('#updateStage');
    if(stage)stage.textContent='Программа закрыта для установки обновления. Новая версия откроется автоматически.';
    try{window.close();}catch{}
   }
  }
 },1200);
}
function targetSettings(){
 const selected=state.config.preferences?.target_niches||[];
 const names=Object.entries(state.config.target_niche_names||{});
 return '<details class="target-settings glass-subpanel" id="targetSettings" '+(state.targetSettingsOpen?'open':'')+'>'+
 '<summary class="target-settings-summary"><span class="target-settings-icon">'+ico('filter')+'</span><span class="target-settings-title"><strong>Приоритетные направления</strong><small>Выбери, какой бизнес показывать в подборке перспективных</small></span>'+
 '<span class="target-settings-count">'+selected.length+' из '+names.length+'</span><span class="target-settings-chevron">⌄</span></summary>'+
 '<div class="target-settings-content"><p class="target-settings-hint">Выбор влияет на приоритеты и уведомления, но не удаляет публикации. Отсутствие ссылки на сайт не доказывает, что его нет.</p>'+
 '<div class="target-options" role="group" aria-label="Приоритетные ниши">'+names.map(([key,title])=>'<label class="target-pill"><input type="checkbox" data-target-niche="'+esc(key)+'" '+(selected.includes(key)?'checked':'')+'><span class="target-pill-label"><span class="target-pill-check">✓</span>'+esc(title)+'</span></label>').join('')+'</div>'+
 '<div class="target-settings-actions"><span class="hint" id="targetSelectionStatus">Выбрано направлений: '+selected.length+'</span><button class="btn primary" data-action="save-target-niches">Сохранить изменения</button></div></div></details>';
}

function visiblePosts(){
 return state.posts.filter(p=>(state.postSource==='all'||String(p.source_id)===String(state.postSource))
   && (state.targetNiche==='all'||p.niche===state.targetNiche)
   && (!state.targetOnly||state.postStatus==='filtered'||qualifiesTarget(p)));
}
function sourceCards(){let f=(state.sourceSearch||'').trim().toLowerCase();let sources=state.sources.filter(s=>!f||`${s.name} ${s.city} ${s.kind}`.toLowerCase().includes(f));return sources.length?sources.map(sourceTile).join(''):noData('Нет совпадений','Добавь паблик или загрузи CSV с подборкой источников.');}

function workspace(){let title,desc,tool='';if(state.view==='ad'){title='Добавить рекламодателя';desc='Сохрани объявление, которое нашёл в местном паблике. Контакты выделяются из текста, повторы учитываются по ссылке.';tool=adForm();}else if(state.view==='map'){title='Map Hunter';desc='Небольшие запросы OpenStreetMap для поиска организаций. Незаполненное поле сайта — не доказательство, что его нет.';tool=mapForm();}else if(state.view==='partner'){title='Партнёрства';desc='Агентства и администраторы пабликов, которые могут рекомендовать тебя своим клиентам.';}else if(state.view==='request'){title='Прямые запросы';desc='Конкретные запросы на разработку и автоматизацию из открытых источников.';}else{title='База лидов';desc='Вся история найденных компаний, проверки, контакты и сделки.';}
 const leads=getFiltered();if(!state.selectedId||!leads.some(l=>l.id===state.selectedId)){state.selectedId=leads[0]?.id||null;state.detail=null;if(state.selectedId)selectLead(state.selectedId,false);}
 return pageTitle('CRM / '+(state.view==='leads'?'02':state.view.toUpperCase()),title,desc,`<button class="btn soft" data-action="import-csv">${ico('upload')} Импорт CSV</button><button class="btn primary" data-action="new-lead">${ico('plus')} Компания</button>`)
 +tool+`<div class="work-grid"><section class="lead-list glass"><div class="list-head"><h2>Компании</h2><span class="chip">${leads.length} записей</span></div><div class="list-controls"><select class="control" id="statusFilter">${options({all:'Все статусы',...statuses},state.filter)}</select><select class="control" id="sortFilter">${options({score:'По приоритету',recent:'Сначала новые',value:'По сумме'},state.sort)}</select></div><div class="list-items">${leads.length?leads.map(leadRow).join(''):noData('Пока ничего нет','Добавь компанию или измени фильтры.')}</div></section><section class="detail glass" id="leadDetail">${state.detail?.id===state.selectedId?details(state.detail):state.selectedId?'<div class="loading">Открываем карточку...</div>':noData('Выбери компанию','Выбери запись слева, чтобы увидеть подробности.')}</section></div>`;}
function leadRow(l){return `<button class="lead-item ${state.selectedId===l.id?'selected':''}" data-select="${l.id}"><span class="mono">${esc(l.name.charAt(0).toUpperCase())}</span><span class="lead-info"><b>${esc(l.name)} ${l.demo?'<span class="chip demo">ДЕМО</span>':''}</b><small>${esc(l.city||'Город не указан')} · ${esc(types[l.source_type]||'Вручную')} ${l.ad_count>1?'· '+l.ad_count+' публикации':''}</small><p>${esc(l.description||'Описание не добавлено')}</p></span><span class="score">${l.score}</span></button>`;}
async function selectLead(id,rerender=true){state.selectedId=Number(id);try{const lead=await api('/api/leads/'+id);if(Number(id)!==state.selectedId)return;state.detail=lead;if(rerender)render();else{const target=$('#leadDetail');if(target)target.innerHTML=details(lead);}}catch(e){toast(e.message,true);}}
function details(l){return `<div class="detail-head"><div><div class="eyebrow">КАРТОЧКА КОМПАНИИ ${l.demo?'· ДЕМО':''}</div><h2>${esc(l.name)}</h2><p style="margin:0">${esc(l.city||'Город не указан')} · ${esc(state.config.niches?.[l.niche]||'Другое')}</p></div><div class="action-group"><span class="score">${l.score}/100</span><button class="icon-button" data-action="edit-lead" title="Редактировать">${ico('edit')}</button></div></div>
 <div class="detail-flags"><span class="chip blue">${esc(types[l.source_type]||'Вручную')}</span><span class="chip">${esc(webStatuses[l.website_status]||'Не проверен')}</span>${l.ad_count>1?`<span class="chip blue">${l.ad_count} рекламных постов</span>`:''}</div>
 ${l.description?`<div class="divide"></div><h3>Чем занимается</h3><p style="white-space:pre-wrap">${esc(l.description)}</p>`:''}
 <div class="divide"></div><h3>Почему стоит проверить</h3><div class="reason-list">${(l.reasons||[]).map(r=>`<div class="reason">${ico('check')} ${esc(r)}</div>`).join('')}</div>
 <div class="divide"></div><h3>Контакты и источники</h3><div class="meta-grid"><div class="meta"><span>Телефон</span><strong>${esc(l.phone||'Не найден')}</strong></div><div class="meta"><span>Email</span><strong>${esc(l.email||'Не найден')}</strong></div><div class="meta"><span>Соцсети</span>${elink(l.social,l.social||'Нет ссылки')}</div><div class="meta"><span>Сайт</span>${elink(l.website,l.website||'Нет ссылки')}</div><div class="meta"><span>Оригинал</span>${elink(l.source_url,'Открыть публикацию')}</div><div class="meta"><span>Источник</span><strong>${esc(l.source_name||'Добавлено вручную')}</strong></div></div><div class="actions"><a class="btn soft small" target="_blank" rel="noopener noreferrer" href="https://yandex.ru/search/?text=${encodeURIComponent(l.name+' '+l.city+' официальный сайт')}">Проверить сайт ${ico('external')}</a><a class="btn quiet small" target="_blank" rel="noopener noreferrer" href="https://yandex.ru/search/?text=${encodeURIComponent(l.name+' '+l.city+' контакты')}">Поиск контактов ${ico('search')}</a></div>
 <div class="divide"></div><h3>Работа с лидом</h3><div class="edit-grid"><label>Этап<select id="detailStatus" class="control">${options(statuses,l.status)}</select></label><label>Сайт<select id="detailWebsite" class="control">${options(webStatuses,l.website_status)}</select></label><label>Потенциал, ₽<input id="detailValue" class="control" type="number" min="0" value="${Number(l.potential_value)||0}"></label><label>Получено, ₽<input id="detailPaid" class="control" type="number" min="0" value="${Number(l.closed_value)||0}"></label></div><div class="actions"><button class="btn soft small" data-action="save-details">Сохранить изменения</button></div>
 ${l.notes?`<div class="divide"></div><h3>Заметки</h3><p style="white-space:pre-wrap">${esc(l.notes)}</p>`:''}
 <div class="divide"></div><div class="panel-header"><h3 style="margin:0">Черновик предложения</h3><button class="btn quiet small" data-action="copy-draft">${ico('copy')} Копировать</button></div><textarea class="draft" id="draftText">${esc(l.draft||'')}</textarea><p class="hint">Текст — только черновик. Массовой рассылки нет; для рекламных обращений требуется согласие адресата.</p>
 <div class="divide"></div><h3>История</h3>${(l.activities||[]).slice(0,20).map(a=>`<div class="activity">${esc(a.note||a.kind)}<small>${date(a.created_at)}</small></div>`).join('')||'<p>Пока пусто.</p>'}<div class="actions"><input id="noteInput" class="control" style="flex:1;min-width:180px" placeholder="Заметка по разговору"><button class="btn soft small" data-action="add-note">Добавить</button></div><div class="divide"></div><button class="btn quiet small" data-action="delete-lead">${ico('trash')} Удалить карточку</button>`;}
function adForm(){return `<div class="tool-box glass"><h2>Зафиксировать рекламу вручную</h2><p>Извлекаем контакты из вставленного текста. Повтор по той же ссылке не увеличивает счётчик публикаций.</p><form id="adForm"><div class="form-grid"><label>Название компании *<input name="name" required placeholder="Мебельная мастерская"></label><label>Город *<input name="city" required placeholder="Тула"></label><label>Ниша<select name="niche">${options(state.config.niches,'furniture')}</select></label><label>Название паблика<input name="source_name" placeholder="Подслушано Тула"></label><label class="full">Ссылка на пост<input name="source_url" placeholder="https://..."></label><label class="full">Текст рекламы<textarea name="description" rows="3" required placeholder="Сюда вставь объявление..."></textarea></label></div><div class="tool-actions"><button class="btn primary" type="submit">Сохранить рекламу</button></div></form></div>`;}
function mapForm(){return `<div class="tool-box glass"><h2>Открытые данные OpenStreetMap</h2><p>Однократный запрос небольшой части города с перерывом от 90 секунд. Нужно перепроверять сайт и контакты каждой компании.</p><form id="osmForm"><div class="form-grid"><label>Город<select name="city">${state.config.cities.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select></label><label>Категория<select name="niche">${state.config.map_categories.map(c=>`<option value="${esc(c)}">${esc(state.config.niches[c]||c)}</option>`).join('')}</select></label></div><div class="tool-actions"><button class="btn primary" id="osmCheck" type="submit">Найти организации</button></div></form></div>`;}
function pipeline(){let groups=Object.entries(statuses),real=actualLeads();return pageTitle('АНАЛИТИКА / 06','Воронка продаж','Простой способ видеть, что происходит с потенциальными проектами.')+`<div class="pipeline">${groups.map(([key,label])=>`<button class="stage glass" data-action="stage" data-stage="${key}"><small>${esc(key.toUpperCase())}</small><strong>${esc(label)}</strong><b>${real.filter(l=>l.status===key).length}</b></button>`).join('')}</div>`;}
function settings(){return pageTitle('О ПРИЛОЖЕНИИ / 07','Настройки и обновления','Приложение хранит лиды на вашем устройстве. Обновления устанавливаются без повторной распаковки.')+`<div class="content-grid"><section class="panel glass"><div class="panel-header"><h2>Обновления VOID LEADS</h2><span class="chip blue">Версия ${esc(state.config.version||'0.4.0')}</span></div><p class="pitch-text">Новые версии устанавливаются через отдельный проверяемый установщик. Перед обновлением сохраняется резервная копия базы данных.</p><div id="updateState" class="hint">Нажми кнопку для проверки новой версии.</div><div class="actions"><button class="btn primary" data-action="check-update">${ico('refresh')} Проверить обновления</button><button class="btn soft" id="installUpdateButton" data-action="install-update" disabled>Установить обновление</button></div><p class="hint">Для автоматических обновлений нужен опубликованный официальный выпуск GitHub Releases. Если канал ещё не подключён, приложение об этом сообщит.</p></section><section class="panel glass"><div class="panel-header"><h2>Работа в фоне</h2><span class="chip blue">Windows</span></div><p class="pitch-text">Программа остаётся в системном трее, даже если закрыть окно браузера. Из трея её можно снова открыть или полностью завершить.</p><label class="settings-check"><input type="checkbox" id="enableNotifications" ${state.config.preferences?.notifications?'checked':''}> Уведомлять о новых подходящих объявлениях</label><label class="settings-check"><input type="checkbox" id="enableStartup" ${state.config.startup_enabled?'checked':''}> Запускать VOID LEADS вместе с Windows</label><p class="hint">Автозапуск выключен до твоего согласия. Без разрешённого VK-токена фоновая проверка VK не заработает.</p><div class="actions"><button class="btn soft small" data-action="test-notification">Проверить уведомление</button></div><p class="hint">Когда найдём новую рекламу с оценкой от 55/100, программа покажет уведомление Windows. Повторы не уведомляются.</p></section><section class="panel glass"><div class="panel-header"><h2>Как работает поиск</h2></div><div class="tip-list"><div class="tip-item">${ico('check')} VK — только официальный API с вашим токеном</div><div class="tip-item">${ico('check')} RSS — только публичные HTTPS-фиды</div><div class="tip-item">${ico('check')} Telegram — ручной импорт доступных сообщений</div><div class="tip-item">${ico('check')} База и история остаются локально</div></div><div class="actions"><button class="btn soft" data-view="public">Источники пабликов</button></div><div class="divide"></div><h2>Резервная копия</h2><p class="pitch-text">База: <code>%USERPROFILE%\\VOID-LEADS\\leads.sqlite3</code>. Перед установкой обновлений создаётся копия в папке <code>backups</code>.</p></section></div>`;}
function openModal(id){const d=$('#'+id);if(!d.open)d.showModal();}
function editModal(l=null){const f=$('#leadForm');f.reset();f.dataset.edit=l?.id||'';$('#leadDialogTitle').textContent=l?'Редактировать компанию':'Новая компания';$('#leadNiche').innerHTML=options(state.config.niches,l?.niche||'other');$('#leadStatus').innerHTML=options(statuses,l?.status||'new');if(l)Object.entries(l).forEach(([k,v])=>{const input=f.elements.namedItem(k);if(input&&['INPUT','SELECT','TEXTAREA'].includes(input.tagName))input.value=v??'';});openModal('leadDialog');}
async function submitLead(e){e.preventDefault();const f=e.target,d=Object.fromEntries(new FormData(f));try{if(f.dataset.edit){await api('/api/leads/'+f.dataset.edit,'PATCH',d);state.selectedId=+f.dataset.edit;}else{const r=await api('/api/leads','POST',d);state.selectedId=r.id;}$('#leadDialog').close();state.detail=null;await load();toast('Компания сохранена');}catch(err){toast(err.message,true);}}
async function submitSource(e){e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{const r=await api('/api/public/sources','POST',data);$('#sourceDialog').close();await load();toast(r.created?'Источник добавлен':'Этот паблик уже добавлен');}catch(err){toast(err.message,true);}}
async function submitPost(e){e.preventDefault();const data=Object.fromEntries(new FormData(e.target));if(data.published_at)data.published_at=new Date(data.published_at).toISOString();try{const r=await api('/api/public/posts/manual','POST',data);$('#postDialog').close();await load();toast(r.created?'Публикация добавлена':'Такая публикация уже есть');}catch(err){toast(err.message,true);}}
async function submitReview(e){e.preventDefault();try{const r=await api(`/api/public/posts/${state.reviewId}/convert`,'POST',{name:$('#reviewName').value});$('#reviewDialog').close();await load();toast(r.created?'Новый лид добавлен':'Публикация привязана к имеющемуся лиду');}catch(err){toast(err.message,true);}}
async function submitAd(e){e.preventDefault();try{const r=await api('/api/capture/ad','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();await load();toast(r.created?'Рекламодатель сохранён':'Реклама привязана к существующей компании');}catch(err){toast(err.message,true);}}
async function submitOsm(e){e.preventDefault();const btn=$('#osmCheck');btn.disabled=true;btn.textContent='Ищем...';try{const r=await api('/api/osm/search','POST',Object.fromEntries(new FormData(e.target)));await load();toast(`OSM: найдено ${r.total}, новых ${r.created}`);}catch(err){toast(err.message,true);}finally{if(btn.isConnected){btn.disabled=false;btn.textContent='Найти организации';}}}
async function changeLead(){if(!state.selectedId)return;try{await api('/api/leads/'+state.selectedId,'PATCH',{status:$('#detailStatus').value,website_status:$('#detailWebsite').value,potential_value:$('#detailValue').value,closed_value:$('#detailPaid').value});state.detail=null;await load();toast('Карточка обновлена');}catch(err){toast(err.message,true);}}
async function addNote(){const note=$('#noteInput').value.trim();if(!note)return;try{await api('/api/leads/'+state.selectedId+'/activity','POST',{note});state.detail=null;await load();toast('Заметка добавлена');}catch(err){toast(err.message,true);}}
async function doImportSources(){const input=document.createElement('input');input.type='file';input.accept='.csv,text/csv';input.onchange=async()=>{if(!input.files[0])return;try{const csv=await input.files[0].text();const r=await api('/api/public/sources/import','POST',{csv});await load();toast(`Паблики: ${r.added} новых, ${r.duplicates} дублей, ${r.invalid} ошибочных строк`);if(r.errors.length)toast(r.errors.slice(0,3).join(' | '),true);}catch(e){toast(e.message,true);}};input.click();}
async function doImportCsv(){const input=document.createElement('input');input.type='file';input.accept='.csv,text/csv';input.onchange=async()=>{if(!input.files[0])return;try{const csv=await input.files[0].text();const r=await api('/api/import/csv','POST',{csv});await load();toast(`Импорт: ${r.created} новых, ${r.updated} повторов`);}catch(err){toast(err.message,true);}};input.click();}
async function handleAction(element){const a=element.dataset.action;const id=element.dataset.id;
 if(a==='source-run-all'){
  element.disabled=true;
  const jobs=[{name:'Wikidata',path:'/api/company/discover',data:{}},
              {name:'Аудит сайтов',path:'/api/company/audit-batch',data:{limit:8,niche:'all'}}];
  if(state.vkDiscovery?.token_available){
    jobs.push({name:'Бизнес-сообщества VK',path:'/api/company/vk/sweep',data:{}});
  }
  const results=await Promise.allSettled(jobs.map(job=>api(job.path,'POST',job.data)));
  const started=[],skipped=[];
  results.forEach((r,i)=>{
    const name=jobs[i].name;
    if(r.status==='rejected'){
      skipped.push(name+': '+r.reason.message);
    }else if(r.value.state==='running'){
      started.push(name);
    }else{
      skipped.push(name+': '+(r.value.message||'новых заданий нет'));
    }
  });
  if(!state.vkDiscovery?.token_available)skipped.push('VK: нужен пользовательский ключ');
  try{await refreshCompany();}catch(e){toast(e.message,true);}
  toast('Запущено: '+(started.join(', ')||'ничего')+'. '+(skipped.length?'Остальные: '+skipped.join('; '):'') ,!started.length);
  if(element.isConnected)element.disabled=false;
  return;
 }

 if(a==='source-query-copy'){
  const key=state.companyNiche==='all'?'furniture':(state.companyNiche||'furniture');
  const terms={furniture:'изготовление кухонь на заказ мебельная фабрика',
    construction:'строительство домов строительная компания',
    industrial:'производство металлоконструкций металлообработка',
    equipment:'аренда спецтехники'};
  const q=(terms[key]||terms.furniture)+' '+(state.companyGeoCity||'Рязань');
  try{await navigator.clipboard.writeText(q);toast('Поисковый запрос скопирован');}
  catch{toast('Не получилось скопировать поисковый запрос',true);}return;
 }

 if(a==='company-vk-token-save'){
  const token=$('#companyVkUserToken')?.value.trim()||'';
  if(token.length<12){toast('Введи VK user token, а не логин или пароль',true);return;}
  element.disabled=true;
  try{await api('/api/vk/user-token','POST',{token});await refreshCompany();toast('VK user token сохранён защищённо');}
  catch(err){toast(err.message,true);element.disabled=false;}
  return;
 }
 if(a==='company-vk-token-delete'){
  if(!confirm('Отключить пользовательский ключ VK? Автопоиск VK будет выключен.'))return;
  try{
    await api('/api/vk/user-token','DELETE');
    const r=await api('/api/preferences','POST',{company_auto_vk:false});
    state.config.preferences=r.preferences;await refreshCompany();
    toast('Пользовательский VK-токен отключён');
  }catch(err){toast(err.message,true);}
  return;
 }
 if(a==='company-vk-sweep'){
  element.disabled=true;
  try{
    const result=await api('/api/company/vk/sweep','POST',{});
    state.vkDiscovery=result;toast(result.message);await refreshCompany();
  }catch(err){toast(err.message,true);element.disabled=false;}return;
 }
 if(a==='company-vk-search'){
  element.disabled=true;
  try{
    const result=await api('/api/company/vk/search','POST',
       {city:state.companyVkCity,niche:state.companyVkNiche});
    state.vkDiscovery=result;toast(result.message);await refreshCompany();
  }catch(err){toast(err.message,true);element.disabled=false;}return;
 }
 if(a==='company-discover'){
  element.disabled=true;
  try{const r=await api('/api/company/discover','POST',{});toast(r.message);await refreshCompany();}
  catch(err){toast(err.message,true);element.disabled=false;}return;
 }
 if(a==='company-import-osm'){
  const file=document.createElement('input');file.type='file';file.accept='.geojson,.json,application/geo+json,application/json';
  file.onchange=async function(){
    if(!file.files?.[0])return;
    if(file.files[0].size>12_000_000){toast('GeoJSON больше 12 МБ — создай выгрузку поменьше',true);return;}
    const city=state.companyGeoCity||'Рязань';
    try{
      toast('Обрабатываю выгрузку OSM…');
      const raw=await file.files[0].text();
      const result=await api('/api/company/import-geojson','POST',{city,geojson:raw});
      toast('OSM: найдено '+result.total+', добавлено '+result.added+', повторных '+result.updated);
      await load();await refreshCompany();
    }catch(err){toast(err.message,true);}
  };
  file.click();return;
 }
 if(a==='company-batch'){
  element.disabled=true;
  try{const result=await api('/api/company/audit-batch','POST',{limit:8,niche:state.companyNiche});
      state.companyJob=result;toast(result.message);await refreshCompany();}
  catch(err){toast(err.message,true);element.disabled=false;}return;
 }
 if(a==='company-copy-draft'){
  const company=state.companyEntries.find(x=>x.id===Number(id));
  if(!company?.audit?.draft)return toast('Черновик пока не подготовлен',true);
  try{await navigator.clipboard.writeText(company.audit.draft);toast('Черновик скопирован');}
  catch{toast('Не удалось скопировать. Выдели текст вручную.',true);}return;
 }
 if(a==='check-update'){
  element.disabled=true;const status=$('#updateState');status.textContent='Проверяем официальный выпуск...';
  try{const res=await api('/api/update/check');status.textContent=res.message+(res.available?` · ${res.latest}`:'');
      const install=$('#installUpdateButton');if(install)install.disabled=!res.available;
  }catch(e){status.textContent='Не удалось проверить обновления: '+e.message;}
  finally{element.disabled=false;}return;
 }
 if(a==='install-update'){
  if(!confirm('Установить новую версию? После проверки установщика программа сама закроется и перезапустится.'))return;
  element.disabled=true;
  try{
   const res=await api('/api/update/install','POST',{});
   const status=$('#updateState');
   if(status)status.textContent=res.message;
   monitorUpdate();
  }catch(e){toast(e.message,true);element.disabled=false;}return;
 }
 if(a==='save-target-niches'){
  const chosen=[...document.querySelectorAll('[data-target-niche]:checked')].map(el=>el.dataset.targetNiche);
  if(!chosen.length){toast('Выбери хотя бы одну нишу',true);return;}
  const result=await api('/api/preferences','POST',{target_niches:chosen});
  state.config.preferences=result.preferences;
  await load();toast('Ниши сохранены');return;
 }
 if(a==='save-vk-token'){
  const input=$('#vkToken');const value=input?.value?.trim();
  if(!value)return toast('Вставь VK-токен, затем нажми «Сохранить»',true);
  await api('/api/vk/token','POST',{token:value});
  if(input)input.value='';
  state.config.vk_token_saved=true;state.config.vk_token_available=true;render();
  toast('Ключ сохранён в защищённом хранилище Windows');return;
 }
 if(a==='delete-vk-token'){
  if(!confirm('Удалить сохранённый ключ? Автоматическая проверка VK остановится.'))return;
  await api('/api/vk/token','DELETE',{});state.config.vk_token_saved=false;state.config.vk_token_available=false;
  render();toast('Ключ удалён');return;
 }
 if(a==='test-notification'){
  const result=await api('/api/notifications/test','POST',{});
  toast(result.ok?'Проверь уведомление Windows':'Уведомление не отправилось — проверь настройки Windows',!result.ok);return;
 }
 if(a==='new-lead')return editModal();if(a==='import-sources')return doImportSources();if(a==='edit-lead')return editModal(state.detail);if(a==='open-source'){return openModal('sourceDialog');}
 if(a==='open-post'){if(!state.sources.length)return toast('Сначала добавь источник',true);$('#postForm').reset();$('#manualPostSource').innerHTML=state.sources.map(s=>`<option value="${s.id}" ${Number(element.dataset.source)===s.id?'selected':''}>${esc(s.name)} (${esc(s.city)})</option>`).join('');return openModal('postDialog');}
 if(a==='check-all'){
  const candidates=state.sources.filter(s=>s.kind!=='manual'&&s.enabled).slice(0,15);
  const token=$('#vkToken')?.value||'';let added=0,checked=0,errors=[];element.disabled=true;
  for(const src of candidates){try{const result=await api('/api/public/check','POST',{source_id:src.id,token:src.kind==='vk'?token:''});added+=result.added;checked++;}catch(err){errors.push(`${src.name}: ${err.message}`);}await new Promise(r=>setTimeout(r,450));}
  await load();toast(`Проверено ${checked}/${candidates.length} источников · новых постов ${added}`);if(errors.length)toast(errors.slice(0,2).join(' | '),true);return;
 }
 if(a==='toggle-source'){const src=state.sources.find(s=>s.id===Number(id));if(!src)return;await api(`/api/public/sources/${id}`,'PATCH',{enabled:src.enabled?0:1});await load();toast(src.enabled?'Источник приостановлен':'Источник включён');return;}
 if(a==='review-source'){state.view='public';state.postStatus='filtered';state.postSource=String(id);await load();return;}
 if(a==='check-source'){const src=state.sources.find(s=>s.id===Number(id));const token=src?.kind==='vk'?$('#vkToken')?.value||'':'';element.disabled=true;try{const r=await api('/api/public/check','POST',{source_id:Number(id),token});await load();toast(`Прочитано ${r.read} · реклама ${r.matched} · отсеяно ${r.filtered||0} (на разбор ${r.reviewable||0}) · старых реклам ${r.archived||0} · новых ${r.added}`);}catch(e){toast(e.message,true);}finally{element.disabled=false;}return;}
 if(a==='remove-source'){if(!confirm('Удалить источник? Сохранённые лиды останутся.'))return;try{await api(`/api/public/sources/${id}`,'DELETE');await load();toast('Источник удалён');}catch(e){toast(e.message,true);}return;}
 if(a==='restore'){const p=state.posts.find(p=>p.id===Number(id));await api(`/api/public/posts/${id}/restore`,'POST',{});state.postStatus=p?.freshness==='archive'?'archive':p?.freshness==='undated'?'undated':'fresh';await load();toast('Публикация возвращена в '+(state.postStatus==='archive'?'архив':'ленту'));return;}
 if(a==='ignore'){await api(`/api/public/posts/${id}/ignore`,'POST');await load();toast('Публикация пропущена');return;}
 if(a==='convert'){const p=state.posts.find(p=>p.id===Number(id));if(!p)return;state.reviewId=p.id;$('#reviewName').value=p.suggested_name||'';openModal('reviewDialog');return;}
 if(a==='save-details')return changeLead();if(a==='add-note')return addNote();if(a==='import-csv')return doImportCsv();
 if(a==='copy-draft'){const el=$('#draftText');if(el){try{await navigator.clipboard.writeText(el.value);toast('Текст скопирован');}catch{el.select();document.execCommand('copy');toast('Текст выделен для копирования');}}return;}
 if(a==='delete-lead'){if(!confirm('Удалить карточку и заметки?'))return;await api('/api/leads/'+state.selectedId,'DELETE');state.selectedId=null;state.detail=null;await load();toast('Карточка удалена');return;}
 if(a==='stage'){route('leads');state.filter=element.dataset.stage;render();}
}
window.addEventListener('DOMContentLoaded',async()=>{try{state.config=await api('/api/config');await load();}catch(e){$('#content').innerHTML=noData('Не удалось запустить VOID LEADS',e.message);return;}
 document.addEventListener('click',async e=>{
  const close=e.target.closest('[data-close]');if(close){$('#'+close.dataset.close).close();return;}
  const nav=e.target.closest('[data-view]');if(nav){route(nav.dataset.view);if(nav.dataset.select){state.selectedId=Number(nav.dataset.select);await selectLead(state.selectedId);}return;}
  const company=e.target.closest('[data-company]');if(company){state.companySelected=Number(company.dataset.company);render();return;}
  const sel=e.target.closest('[data-select]');if(sel){await selectLead(sel.dataset.select);return;}
  const act=e.target.closest('[data-action]');if(act){try{await handleAction(act);}catch(err){toast(err.message,true);}}
 });
 document.addEventListener('submit',async e=>{const id=e.target.id;if(!['leadForm','sourceForm','postForm','reviewForm','adForm','osmForm'].includes(id))return;e.preventDefault();if(id==='leadForm')return submitLead(e);if(id==='sourceForm')return submitSource(e);if(id==='postForm')return submitPost(e);if(id==='reviewForm')return submitReview(e);if(id==='adForm')return submitAd(e);if(id==='osmForm')return submitOsm(e);});
 document.addEventListener('toggle',e=>{if(e.target.id==='targetSettings')state.targetSettingsOpen=e.target.open;},true);
 document.addEventListener('input',e=>{if(e.target.matches('[data-target-niche]')){const label=$('#targetSelectionStatus');if(label)label.textContent='Выбрано направлений: '+document.querySelectorAll('[data-target-niche]:checked').length;}if(e.target.id==='sourceSearch'){state.sourceSearch=e.target.value;const el=$('#sourceCards');if(el)el.innerHTML=sourceCards();}});
 document.addEventListener('change',async e=>{if(e.target.id==='companyGeoCity'){state.companyGeoCity=e.target.value;}
 if(e.target.id==='companyVkCity'){state.companyVkCity=e.target.value;}
 if(e.target.id==='companyVkNiche'){state.companyVkNiche=e.target.value;}
 if(e.target.id==='companyAutoVK'){
   try{
     const r=await api('/api/preferences','POST',{company_auto_vk:e.target.checked});
     state.config.preferences=r.preferences;state.vkDiscovery.enabled=e.target.checked;
     toast(e.target.checked?'Автоматический поиск компаний VK включён':'Автопоиск VK выключен');
   }catch(err){e.target.checked=!e.target.checked;toast(err.message,true);}
 }
 if(e.target.id==='companyAutoAudit'){
   try{
     const r=await api('/api/preferences','POST',{company_auto_audit:e.target.checked});
     state.config.preferences=r.preferences;state.discovery.auto_audit=e.target.checked;
     toast(e.target.checked?'Ежедневный автоаудит включён':'Ежедневный автоаудит отключён');
   }catch(err){e.target.checked=!e.target.checked;toast(err.message,true);}
 }
 if(e.target.id==='companyAutoDiscovery'){
   try{
     const r=await api('/api/preferences','POST',{company_auto_discovery:e.target.checked});
     state.config.preferences=r.preferences;state.discovery.enabled=e.target.checked;
     toast(e.target.checked?'Еженедельный автопоиск включён':'Еженедельный автопоиск отключён');
   }catch(err){e.target.checked=!e.target.checked;toast(err.message,true);}
 }
 if(e.target.id==='companyNiche'){state.companyNiche=e.target.value;state.companySelected=null;render();}
 if(e.target.id==='statusFilter'){state.filter=e.target.value;state.selectedId=null;state.detail=null;render();}if(e.target.id==='sortFilter'){state.sort=e.target.value;render();}if(e.target.id==='postStatus'){state.postStatus=e.target.value;await load();}if(e.target.id==='postSource'){state.postSource=e.target.value;render();}if(e.target.id==='targetNiche'){state.targetNiche=e.target.value;render();}if(e.target.id==='targetOnly'){state.targetOnly=e.target.checked;render();}
 if(e.target.id==='enableNotifications'||e.target.id==='enableStartup'){
  const key=e.target.id==='enableNotifications'?'notifications':'startup';
  try{
   const res=await api('/api/preferences','POST',{[key]:e.target.checked});
   state.config.preferences=res.preferences;
   state.config.startup_enabled=res.startup_enabled;
   toast('Настройка сохранена');
  }catch(err){e.target.checked=!e.target.checked;toast(err.message,true);}
 }if(e.target.id==='sourceKind'){const i=$('#sourceLocatorRow input');if(i)i.placeholder=e.target.value==='manual'?'https://t.me/channel':e.target.value==='rss'?'https://example.org/rss.xml':'https://vk.com/overhear.tula';}});
 setInterval(()=>{if(state.view==='company'&&(state.companyJob.state==='running'||state.discovery.state==='running'||state.vkDiscovery.state==='running'))refreshCompany().catch(()=>{});},2500);
 setInterval(()=>{if(document.visibilityState==='visible'&&!document.querySelector('dialog[open]')&&!document.querySelector('#targetSettings[open]')&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))load().catch(()=>{});},60000);
 $('#createLead').addEventListener('click',()=>editModal());$('#menuBtn').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
 $('#search').addEventListener('input',e=>{state.search=e.target.value;state.selectedId=null;state.detail=null;if(!['leads','ad','map','partner','request'].includes(state.view))state.view='leads';render();});
 document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();}if(e.key==='Escape')$('#sidebar').classList.remove('open');});
});
