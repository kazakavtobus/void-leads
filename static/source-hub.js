'use strict';
/* Source Hub: distinguish working integrations from research links.
   Never claim manual websites are APIs or automatically ingested. */
function sourceHub(){
  const d=state.discovery||{}, v=state.vkDiscovery||{}, p=state.publicStats||{};
  const city=state.companyGeoCity||'Рязань';
  const niche=state.companyNiche==='all'?'furniture':(state.companyNiche||'furniture');
  const words={furniture:'изготовление кухонь на заказ мебельная фабрика',
    construction:'строительство домов строительная компания',
    industrial:'производство металлоконструкций металлообработка',
    equipment:'аренда спецтехники'};
  const query=(words[niche]||words.furniture)+' '+city;
  const yandex='https://yandex.ru/search/?text='+encodeURIComponent(query);
  const google='https://www.google.com/search?q='+encodeURIComponent(query);
  const channels=[
    {name:'Wikidata',kind:'auto',status:d.enabled?'Включена':'Доступна · выключена',
     note:'Официальная открытая база; ограниченный поиск предприятий раз в неделю',action:'company-discover',actionName:'Запустить'},
    {name:'Сообщества VK',kind:'auto',status:v.token_available?(v.enabled?'Автопоиск включён':'Ключ подключён'):'Нужен user-токен',
     note:'Три города × три ниши, проверка города в карточке компании',action:'company-vk-sweep',actionName:'Поиск 9 направлений',disabled:!v.token_available},
    {name:'VK паблики',kind:'feeds',status:(p.sources||0)>0?'Есть источники':'Нужны паблики',
     note:'Реклама локальных компаний в разрешённых источниках VK',view:'public',actionName:'Открыть Public Hunter'},
    {name:'Отраслевые RSS',kind:'feeds',status:(p.sources||0)>0?'Настраиваются в Public Hunter':'Нужны RSS-ленты',
     note:'Отраслевые публикации и объявления — только подключённые RSS',view:'public',actionName:'Добавить RSS'},
    {name:'OpenStreetMap',kind:'import',status:'Локальная выгрузка',
     note:'Городские GeoJSON с указанием OSM ID; источник и дубли сохраняются',action:'company-import-osm',actionName:'Импорт GeoJSON'},
    {name:'Map Hunter',kind:'import',status:'По запросу',
     note:'Небольшие разовые выборки по OpenStreetMap, без массового Overpass',view:'map',actionName:'Открыть Map Hunter'},
    {name:'Яндекс — поиск сайтов',kind:'manual',status:'Ручной анализ',
     note:'Поиск фабрик и производителей по городу и нише',url:yandex,actionName:'Найти компании'},
    {name:'Google — поиск сайтов',kind:'manual',status:'Ручной анализ',
     note:'Дополнительная выдача, уточнение сайта и реализованных проектов',url:google,actionName:'Найти компании'},
    {name:'2ГИС',kind:'manual',status:'Ручной просмотр',
     note:'Каталог компаний; API и выгрузки требуют отдельной лицензии',url:'https://2gis.ru/',actionName:'Открыть 2ГИС'},
    {name:'Яндекс Карты',kind:'manual',status:'Ручной просмотр',
     note:'Каталог организаций; коммерческий API и хранение данных лицензируются отдельно',url:'https://yandex.ru/maps/',actionName:'Открыть Карты'},
    {name:'Авито',kind:'manual',status:'Ручной просмотр',
     note:'Местные изготовители кухонь, мебели и строительные бригады; без парсинга',url:'https://www.avito.ru/',actionName:'Открыть Авито'},
    {name:'Торгово-промышленная палата',kind:'manual',status:'Ручной просмотр',
     note:'Региональные палаты и каталоги предприятий, без автоматического копирования данных',url:'https://tpprf.ru/',actionName:'Открыть ТПП'},
    {name:'2ГИС Places API',kind:'gated',status:'Нужна лицензия/демоключ',
     note:'Демоключ доступен через кабинет; права на сохранение CRM уточняются отдельно',url:'https://dev.2gis.ru/api/catalog',actionName:'Условия подключения'},
  ];
  const categories={auto:'Автопоиск',feeds:'Мониторинг',import:'Открытые данные',manual:'Проверка вручную',gated:'По лицензии'};
  const linked=c=>c.view?'<button class="btn soft small" data-view="'+esc(c.view)+'">'+esc(c.actionName)+' →</button>':
    c.action?'<button class="btn soft small" data-action="'+esc(c.action)+'" '+(c.disabled?'disabled':'')+'>'+
       esc(c.actionName)+' →</button>':
    '<a class="btn soft small" href="'+esc(safeLink(c.url))+'" target="_blank" rel="noopener noreferrer">'+esc(c.actionName)+' ↗</a>';
  const display=c=>'<article class="source-hub-entry"><div class="source-hub-name"><strong>'+esc(c.name)+'</strong>'+
    '<span class="source-hub-state">'+esc(c.status)+'</span></div>'+
    '<p>'+esc(c.note)+'</p>'+linked(c)+'</article>';
  const enabled=channels.filter(c=>c.kind==='auto'&&(c.status.includes('включён')||c.status.includes('Включена'))).length;
  return '<details class="company-source-hub glass"><summary><span><span class="eyebrow">ИСТОЧНИКИ КЛИЕНТОВ</span>'+
    '<strong>Source Hub · '+channels.length+' каналов</strong><small>'+enabled+
    ' автоматических источника включено · остальные требуют настройки, импорта или проверки вручную</small>'+
    '</span><span class="source-hub-arrow">▾</span></summary>'+
    '<div class="source-hub-top"><p>Список показывает реальный способ доступа к каждому источнику. '+
    '«Открыть» не означает, что канал автоматически собирает данные. Для подбора компаний в поисковиках используем запрос:</p>'+
    '<code>'+esc(query)+'</code><button class="btn quiet small" data-action="source-query-copy">Скопировать запрос</button></div>'+
    '<div class="source-hub-grid">'+channels.map(display).join('')+'</div>'+
    '<p class="source-hub-footer">Не подключаем запрещённый массовый сбор и не рассылаем сообщения автоматически. '+
    'Нельзя считать компанию готовой платить только на основании рекламы, вакансий или старого дизайна.</p></details>';
}
