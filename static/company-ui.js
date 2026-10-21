'use strict';
/* Company Hunter — self-contained view integrated with the existing CRM globals. */
async function refreshCompany(){
 const response=await api('/api/company/queue');
 state.companyEntries=response.companies||[];
 state.companyJob=response.job||{state:'idle'};
 if(state.view==='company')render();
}
function companyHunter(){
 const companies=(state.companyEntries||[]).filter(function(c){
  return state.companyNiche==='all'||c.niche===state.companyNiche;
 });
 const inspected=companies.filter(function(c){return c.audit&&c.audit.status==='analyzed';}).length;
 const selected=companies.find(function(c){return c.id===state.companySelected;})||companies[0];
 const job=state.companyJob||{state:'idle'};
 const running=job.state==='running';
 const niches={all:'Все направления',furniture:'Мебель и кухни',construction:'Строительство',
               industrial:'Производство',equipment:'Спецтехника'};
 function card(c){
  const a=c.audit||{};
  const checked=a.status==='analyzed';
  const stateLabel=checked?'HTML проверен':a.status==='unavailable'?'Нужна ручная проверка':
     (c.website?'Ожидает проверки':'Сайт не указан');
  return '<button class="company-row '+(selected&&c.id===selected.id?'selected':'')+
    '" data-company="'+c.id+'" type="button">'+
    '<span class="company-row-icon">'+esc((c.name||'?').charAt(0))+'</span>'+
    '<span class="company-row-info"><strong>'+esc(c.name)+'</strong><small>'+
    esc(c.city||'Город не указан')+' · '+esc((state.config.niches||{})[c.niche]||c.niche||'Другое')+
    '</small><span class="company-row-state">'+esc(stateLabel)+'</span></span>'+
    (checked?'<span class="company-score">'+a.score+'<small>/100</small></span>':'')+'</button>';
 }
 function evidence(items,empty){
  return items&&items.length?
    '<ul class="company-evidence">'+items.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>':
    '<p class="hint">'+esc(empty)+'</p>';
 }
 function detail(c){
  if(!c)return noData('Добавь первые компании','Импортируй CSV в базу лидов или воспользуйся Map Hunter.');
  const a=c.audit||{}, findings=a.findings||[], ideas=a.opportunities||[];
  return '<div class="eyebrow">КОММЕРЧЕСКАЯ ГИПОТЕЗА</div><h2>'+esc(c.name)+'</h2>'+
    '<p class="company-subtitle">'+esc(c.city||'Город не указан')+' · '+
    esc((state.config.niches||{})[c.niche]||'Другая ниша')+'</p>'+
    '<div class="company-detail-links">'+elink(c.website,'Открыть сайт')+
    (c.source_url?elink(c.source_url,'Источник'):'')+
    '<button class="btn quiet small" data-view="leads" data-select="'+c.id+'">Открыть CRM →</button></div>'+
    '<div class="company-section"><h3>Что действительно обнаружено</h3>'+
    evidence(findings,'Сайт ещё не анализировался. Запусти пакетную проверку.')+'</div>'+
    '<div class="company-section"><h3>Возможные улучшения</h3>'+
    evidence(ideas,'Пока нет подтверждённых наблюдений, на которых можно строить предложение.')+'</div>'+
    (a.draft?'<div class="company-draft"><h3>Черновик сообщения</h3><p>'+esc(a.draft)+
      '</p><button class="btn soft small" data-action="company-copy-draft" data-id="'+c.id+'">'+
      ico('copy')+' Скопировать черновик</button></div>':'')+
    '<p class="hint">'+esc(a.disclaimer||'Проверка пока не запускалась.')+'</p>';
 }
 return pageTitle('ПОИСК / 02','Company Hunter',
    'Анализируем сайты компаний из CRM и ищем конкретные поводы для полезного предложения. Без автоматических рассылок.',
    '<button class="btn soft" data-view="map">'+ico('map')+' Найти компании в OSM</button>'+
    '<button class="btn primary" data-action="company-batch" '+(running?'disabled':'')+'>'+
    ico('refresh')+' '+(running?'Проверка идёт…':'Проверить до 8 сайтов')+'</button>')+
    '<div class="notes-grid">'+
      '<div class="note-kpi glass"><strong>'+companies.length+'</strong><span>Компаний в выборке</span></div>'+
      '<div class="note-kpi glass"><strong>'+inspected+'</strong><span>HTML проверен</span></div>'+
      '<div class="note-kpi glass"><strong>'+(running?job.done+'/'+job.total:'—')+
      '</strong><span>'+(running?'Идёт анализ сайтов':'Пакетная проверка')+'</span></div></div>'+
    '<div class="company-message glass">'+ico('shield')+
    '<div><strong>Оценка — не вероятность продажи</strong><p>Это предварительный приоритет по признакам HTML. '+
    'Пока не проверяются внешний вид, мобильная верстка, работа JS-форм и внутренняя CRM. '+
    'Отсутствие ссылки на сайт не означает отсутствие сайта у компании. Повторная проверка — через 7 дней.</p></div></div>'+
    '<div class="company-work-grid"><section class="company-list glass">'+
    '<div class="company-list-head"><h2>Кандидаты</h2><span class="chip">'+companies.length+' шт.</span></div>'+
    '<label class="company-filter-label">Отрасль<select class="control" id="companyNiche">'+
    options(niches,state.companyNiche)+'</select></label>'+
    '<div class="company-rows">'+(companies.length?companies.map(card).join(''):
      noData('Компаний пока нет','Добавь бизнесы в CRM и обнови страницу.'))+'</div></section>'+
    '<section class="company-detail glass">'+detail(selected)+'</section></div>';
}
