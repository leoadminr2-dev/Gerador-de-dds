'use strict';
const state = {employees:[],history:[],units:[],work_sites:[],unit_code:'GIG',shifts:[],supervisors:{},capacity:107,topics:[],topicCategories:[]};
const BROWSER_BACKUP_KEY = 'gerador-dds.browser-backup.v1';
const $ = (id)=>document.getElementById(id);
const dayISO = ()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const esc = (v)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const shiftLabel = s => ({NOTURNO:'Madrugada',DIURNO:'Manhã',VESPERTINO:'Tarde'}[s]||'Sem turno');
const unitLabel = s => !s?'Selecione uma unidade':s;
const sectorLabel = s => !s?'LIMPEZA':s;
const norm = s => String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase().trim();
let lastPdfUrl = '';
let pendingGeneratePayload = null;
let editingEmployeeId = null;

function toast(message,type='success'){const node=document.createElement('div');node.className=`toast ${type==='error'?'error':type==='success'?'success':''}`;node.textContent=message;$('toast-region').appendChild(node);setTimeout(()=>node.remove(),4800)}
function showError(message){$('form-error').textContent=message;$('form-error').classList.remove('hidden')}
function clearError(){$('form-error').textContent='';$('form-error').classList.add('hidden')}
function readBrowserBackup(){
 try{
  const raw=localStorage.getItem(BROWSER_BACKUP_KEY);if(!raw)return null;
  const backup=JSON.parse(raw);
  return backup?.format==='gerador-dds-backup'&&backup?.backup_version===1&&backup?.state&&Array.isArray(backup.state.employees)?backup:null;
 }catch(_){return null}
}
async function refreshBrowserBackup(){
 try{
  const response=await fetch('/api/backup-data',{cache:'no-store',headers:{'Accept':'application/json'}});
  if(!response.ok)return false;
  const backup=await response.json();
  if(backup?.format!=='gerador-dds-backup'||backup?.backup_version!==1||!Array.isArray(backup?.state?.employees))return false;
  localStorage.setItem(BROWSER_BACKUP_KEY,JSON.stringify(backup));
  return true;
 }catch(err){
  console.warn('Não foi possível atualizar a cópia de segurança deste navegador.',err);
  return false;
 }
}
async function api(url,options={}){
 const response=await fetch(url,options);let body={};try{body=await response.json()}catch{}
 if(!response.ok)throw new Error(body.error||`Falha na solicitação (${response.status}).`);
 const method=String(options.method||'GET').toUpperCase();
 if(['POST','PUT','PATCH','DELETE'].includes(method))await refreshBrowserBackup();
 return body;
}

let topicSearchTimer=null;
function resetTopicEditor(){
 $('topic-create-form')?.reset();
 if($('editing-topic-id'))$('editing-topic-id').value='';
 if($('save-topic-btn'))$('save-topic-btn').textContent='Salvar tema na biblioteca →';
 $('cancel-topic-edit')?.classList.add('hidden');
}
function startEditTopic(id){
 const t=state.topics.find(x=>x.id===id);if(!t)return;
 $('editing-topic-id').value=String(t.id);$('new-topic-title').value=t.title;$('new-topic-category').value=t.category;
 $('new-topic-objective').value=t.objective;$('new-topic-content').value=t.content;
 $('save-topic-btn').textContent='Salvar alterações do tema →';$('cancel-topic-edit').classList.remove('hidden');
 $('topic-create-form').scrollIntoView({behavior:'smooth',block:'start'});$('new-topic-title').focus();
 toast('Tema aberto para edição. As mudanças serão salvas na biblioteca de temas.');
}

async function loadTopics(){
 try{
  const q=$('topic-search')?.value||'',category=$('topic-category')?.value||'TODAS';
  const data=await api(`/api/topics?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`);
  state.topics=data.topics||[];state.topicCategories=data.categories||[];
  const storageLabel=$('topic-storage-label');if(storageLabel)storageLabel.textContent=data.storage_label||'Banco de temas';
  const cat=$('topic-category');if(cat){const previous=cat.value;cat.innerHTML='<option value="TODAS">Todas as categorias</option>'+state.topicCategories.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');cat.value=state.topicCategories.includes(previous)?previous:'TODAS'}
  const datalist=$('topic-options');if(datalist)datalist.innerHTML=state.topics.map(t=>`<option value="${esc(t.title)}"></option>`).join('');
  const count=$('topic-count');if(count)count.textContent=`${data.count} tema${data.count===1?'':'s'} disponível${data.count===1?'':'eis'} neste ambiente`;
   const list=$('topic-library');if(list)list.innerHTML=state.topics.length?state.topics.map(t=>{
    const sources=Array.isArray(t.sources)?t.sources:[];
    const updated=t.updated_at?new Date(t.updated_at).toLocaleDateString('pt-BR'):'';
    const sourceLinks=sources.length?`<details class="topic-card-sources"><summary>${sources.length} referência(s) online</summary><ul>${sources.map(src=>`<li><a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.title||src.url)}</a>${src.authority?`<small>${esc(src.authority)}</small>`:''}</li>`).join('')}</ul></details>`:'';
    const sourceNote=t.source_status==='offline-draft'?'<p class="topic-source-note">Rascunho não verificado online. Confirme as fontes oficiais e os procedimentos locais antes de usar.</p>':'';
    return `<article class="topic-card"><div class="topic-card-top"><span class="topic-category">${esc(t.category)}</span><span class="topic-source">${esc(t.source_label||'Tema cadastrado')}</span></div><h3>${esc(t.title)}</h3><p>${esc(t.objective)}</p>${updated?`<small class="topic-updated">Salvo/atualizado em ${esc(updated)}</small>`:''}${sourceLinks}${sourceNote}<div class="topic-card-actions"><button class="button primary small" data-use-topic="${t.id}">Usar neste DDS</button><button class="button secondary small" data-edit-topic="${t.id}">Editar conteúdo</button>${t.source==='cadastrado pelo usuário'?`<button class="button secondary small" data-delete-topic="${t.id}">Excluir</button>`:''}</div></article>`;
   }).join(''):'<div class="empty-state">Nenhum tema encontrado. Ajuste a busca ou cadastre um novo tema.</div>';
  list?.querySelectorAll('[data-use-topic]').forEach(b=>b.addEventListener('click',()=>useTopic(Number(b.dataset.useTopic))));
  list?.querySelectorAll('[data-edit-topic]').forEach(b=>b.addEventListener('click',()=>startEditTopic(Number(b.dataset.editTopic))));
  list?.querySelectorAll('[data-delete-topic]').forEach(b=>b.addEventListener('click',()=>removeTopic(Number(b.dataset.deleteTopic))));
 }catch(err){const list=$('topic-library');if(list)list.innerHTML=`<div class="empty-state">Não foi possível abrir o banco de temas: ${esc(err.message)}</div>`;toast(err.message,'error')}
}
function useTopic(id){const t=state.topics.find(x=>x.id===id);if(!t)return;$('topic').value=t.title;$('content').value=t.content;$('content').dispatchEvent(new Event('input'));refreshSummary();navigate('novo');toast('Tema e conteúdo carregados da biblioteca de temas. Revise antes de gerar o PDF.')}
async function removeTopic(id){if(!confirm('Excluir este tema cadastrado? Essa ação não pode ser desfeita.'))return;try{await api(`/api/topics/${id}`,{method:'DELETE'});await loadTopics();toast('Tema removido da biblioteca de temas.')}catch(err){toast(err.message,'error')}}
$('topic-search')?.addEventListener('input',()=>{clearTimeout(topicSearchTimer);topicSearchTimer=setTimeout(loadTopics,180)});
$('topic-category')?.addEventListener('change',loadTopics);
$('browse-topics')?.addEventListener('click',()=>navigate('temas'));
$('topic')?.addEventListener('change',()=>{const exact=state.topics.find(t=>norm(t.title)===norm($('topic').value));if(exact){$('content').value=exact.content;$('content').dispatchEvent(new Event('input'));toast('Conteúdo carregado da biblioteca de temas.')}else analyzeCriticalContent()});
$('topic-create-form')?.addEventListener('submit',async e=>{e.preventDefault();const id=$('editing-topic-id').value;const data={title:$('new-topic-title').value,category:$('new-topic-category').value,objective:$('new-topic-objective').value,content:$('new-topic-content').value};try{const r=await api(id?`/api/topics/${encodeURIComponent(id)}`:'/api/topics',{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});resetTopicEditor();await loadTopics();toast(id?`Tema “${r.topic.title}” atualizado na biblioteca de temas.`:`Tema “${r.topic.title}” salvo na biblioteca de temas.`)}catch(err){toast(err.message,'error')}});
$('cancel-topic-edit')?.addEventListener('click',resetTopicEditor);

function navigate(page){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===`page-${page}`));document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===page));window.scrollTo({top:0,behavior:'smooth'});if(page==='historico')renderHistory();if(page==='colaboradores')renderRoster();if(page==='temas')loadTopics()}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.goto)));

function employeesForShiftAndUnit(){const shift=$('shift').value;const site=$('operational-unit').value;return state.employees.filter(e=>(!shift||e.shift===shift)&&norm(e.work_site||e.operational_unit||e.sector||'')===norm(site))}
function renderEmployeeSelection(){const box=$('employee-selection-panel'),list=$('employee-selection-list'),all=$('use-all-shift-employees');if(!box||!list||!all)return;const eligible=employeesForShiftAndUnit();box.classList.toggle('hidden',all.checked);$('employee-selection-hint').textContent=all.checked?'Todos os colaboradores da unidade e turno selecionados serão incluídos automaticamente.':'Desmarque ou marque nomes individualmente para definir a lista de presença.';list.innerHTML=eligible.length?eligible.map(e=>'<label class="employee-selection-item"><input type="checkbox" name="selected-employee" value="'+esc(String(e.id))+'" checked /><span>'+esc(e.name)+'</span></label>').join(''):'<p class="field-hint">Nenhum colaborador cadastrado para esta unidade e turno.</p>';list.querySelectorAll('input[name="selected-employee"]').forEach(input=>input.addEventListener('change',refreshSummary))}
function filteredEmployees(){const eligible=employeesForShiftAndUnit();if($('use-all-shift-employees')?.checked!==false)return eligible;const selected=new Set(Array.from(document.querySelectorAll('input[name="selected-employee"]:checked')).map(input=>input.value));return eligible.filter(e=>selected.has(String(e.id)))}
function refreshSummary(){const shift=$('shift').value;const unit=$('operational-unit').value;const results=filteredEmployees();const requiredRows=results.length+5;let physicalCapacity=0;for(const pageSize of [23,42,42]){physicalCapacity+=pageSize;if(requiredRows<=physicalCapacity)break;}while(requiredRows>physicalCapacity)physicalCapacity+=42;$('mini-topic').textContent=$('topic').value.trim().toUpperCase()||'TEMA DO DDS';$('summary-sector').textContent=unitLabel(unit);$('summary-unit').textContent='GALEÃO';$('mini-unit').textContent=unit;document.querySelectorAll('.mini-participant-unit').forEach(el=>el.textContent=unit);$('summary-shift').textContent=shift?shiftLabel(shift):'Aguardando seleção';$('summary-count').textContent=`${results.length} colaborador${results.length===1?'':'es'} encontrado${results.length===1?'':'s'}`;$('capacity-label').textContent=`${requiredRows} linhas (${results.length} nomes + 5 vazias)`;$('capacity-fill').style.width=`${Math.min(requiredRows/physicalCapacity*100,100)}%`;
 const unitKey=norm(unit);const fixedSupervisors={'UNITED':['WILKER MARIA'],'TECA':['PEDRO PAULO'],'GATE GOURMET':['MARCIA FASUTINO']};const supervisors=unitKey==='GIG'?(shift?(state.supervisors[shift]||[]):[]):(fixedSupervisors[unitKey]||[]);$('supervisor-value').textContent=supervisors.length?supervisors.join(' / '):(unitKey==='GIG'?'Selecione um turno para consultar':'Selecione uma unidade');
 const callout=$('roster-callout');if(state.employees.length===0){callout.classList.remove('hidden');callout.innerHTML='<span>!</span><div><b>Planilha de colaboradores ainda não carregada</b><p>Importe a lista na área “Colaboradores” para preencher os nomes automaticamente.</p><button type="button" class="text-button" data-goto="colaboradores">Importar agora →</button></div>';callout.querySelector('[data-goto]').addEventListener('click',()=>navigate('colaboradores'))}else if(!state.employees.some(e=>e.shift===shift&&norm(e.work_site||'')===norm(unit))){callout.classList.remove('hidden');callout.innerHTML='<span>!</span><div><b>Nenhum colaborador cadastrado para este turno e unidade</b><p>Abra Colaboradores e confira o turno e a unidade de cada pessoa. O DDS não incluirá colaboradores de outra unidade ou de outro turno.</p></div>'}else if(shift&&results.length===0){callout.classList.remove('hidden');callout.innerHTML='<span>!</span><div><b>Nenhum colaborador encontrado para a seleção</b><p>Confira o turno e a unidade escolhidos. O DDS não incluirá nomes de outra seleção.</p></div>'}else{callout.classList.add('hidden')}
}
$('use-all-shift-employees').addEventListener('change',()=>{renderEmployeeSelection();refreshSummary()});
$('select-all-visible-employees').addEventListener('click',()=>{document.querySelectorAll('input[name="selected-employee"]').forEach(input=>input.checked=true);refreshSummary()});
['topic','dds-date','duration'].forEach(id=>$(id).addEventListener('input',refreshSummary));
['shift','operational-unit'].forEach(id=>$(id).addEventListener('change',()=>{renderEmployeeSelection();refreshSummary()}));
['shift','operational-unit','dds-date','duration'].forEach(id=>$(id).addEventListener('change',refreshSummary));
$('dds-date').value='';
$('content').addEventListener('input',()=>{$('char-count').textContent=`${$('content').value.length} / 2600`;if($('content').value.trim()){$('content-status').classList.add('ready');$('content-status').innerHTML='<span class="status-dot"></span> Conteúdo preenchido'}else{$('content-status').classList.remove('ready');$('content-status').innerHTML='<span class="status-dot"></span> Aguardando tema'}analyzeCriticalContent()});

function suggestForTopic(topic){const t=norm(topic);let body;
 if(/DERRAMAMENTO|ORIGEM DESCONHECIDA|SUBSTANCIA DESCONHECIDA/.test(t)) body=`RISCO E CENÁRIO
Uma substância desconhecida pode causar exposição química ou reação perigosa em local com passageiros e equipes.

OBJETIVO
Evitar exposição e impedir que pessoas entrem na área afetada.

CONDUTA SEGURA
Não toque, não cheire de perto nem tente neutralizar com outro produto. Afaste pessoas com segurança, sinalize/isole quando puder fazer isso sem exposição e acione a liderança e a equipe de resposta definida no aeroporto. Não retome a limpeza sem orientação e liberação.

MENSAGEM FINAL
Em derramamento desconhecido, proteger e comunicar vem antes de limpar.`;
 else if(/CONTAMINACAO CRUZADA|CRUZADA NA LIMPEZA/.test(t)) body=`RISCO E CENÁRIO
Panos, mops, luvas e carrinhos usados sem controle podem transferir sujidade entre sanitários, áreas de alimentação e superfícies de contato.

OBJETIVO
Evitar a transferência de contaminantes entre ambientes.

CONDUTA SEGURA
Siga a sequência do procedimento local; utilize materiais separados conforme a identificação adotada pela empresa; higienize ou substitua utensílios quando indicado; troque luvas quando necessário e higienize as mãos. Nunca leve material usado em sanitários para outras áreas.

MENSAGEM FINAL
Separe os materiais e respeite a sequência para não espalhar a contaminação.`;
 else if(/SANITARIO|BANHEIRO|ALTO FLUXO/.test(t)) body=`RISCO E CENÁRIO
Sanitários aeroportuários têm uso intenso e circulação contínua. Sinalização insuficiente, mistura de materiais ou produto incorreto pode expor trabalhadores e usuários.

OBJETIVO
Executar a limpeza com controle de circulação, higiene e qualidade.

CONDUTA SEGURA
Siga a sequência prevista; use produto autorizado para a finalidade; respeite diluição e tempo de contato do rótulo; mantenha a sinalização até o piso estar seguro; não misture saneantes; reponha insumos e comunique avarias ou falta de material.

MENSAGEM FINAL
Qualidade exige método, sinalização e conferência final.`;
 else if(/EMBARQUE|CIRCULACAO|PASSAGEIRO|AEROPORT|AREA OPERACIONAL|PATIO|PORTAO/.test(t)) body=`RISCO E CENÁRIO
A limpeza pode ocorrer próxima a passageiros, bagagens, carrinhos e, em áreas restritas, veículos e equipamentos. A intervenção mal posicionada pode bloquear rotas ou expor pessoas.

OBJETIVO
Manter o ambiente limpo sem criar obstáculos nem acessar áreas sem autorização.

CONDUTA SEGURA
Planeje a tarefa; mantenha saídas, rotas acessíveis e equipamentos de emergência desobstruídos; sinalize conforme o procedimento; conduza carrinhos sem bloquear a visão; respeite barreiras, autorizações e rotas locais. Se o fluxo impedir uma atividade segura, pare e combine a intervenção com a liderança.

MENSAGEM FINAL
A limpeza deve proteger pessoas e preservar a circulação segura.`;
 else if(/ALCOOL|BEBIDA|EMBRIAG|DROGA|SUBSTANC/.test(t)) body=`RISCO E CENÁRIO
Álcool e outras substâncias podem reduzir a atenção, os reflexos e a capacidade de avaliar riscos, afetando o trabalhador, colegas e a operação.

OBJETIVO
Reforçar escolhas seguras antes do deslocamento e do início das atividades.

CONDUTA SEGURA
Nunca conduza após consumir álcool ou substância que afete a capacidade. Planeje transporte seguro e procure a liderança se perceber uma condição que coloque alguém em risco. Siga as políticas e canais internos de orientação.

MENSAGEM FINAL
A segurança começa antes de chegar ao unidade.`;
 else if(/EPI|PROTECAO INDIVIDUAL|LUVA|OCULOS|MASCARA|BOTINA/.test(t)) body=`RISCO E CENÁRIO
Na limpeza aeroportuária, o EPI indicado pode reduzir a exposição a respingos, agentes biológicos, produtos químicos e outros riscos previstos na tarefa.

OBJETIVO
Reforçar seleção, inspeção, ajuste e conservação do EPI correto.

CONDUTA SEGURA
Confira o estado do equipamento antes do uso. Utilize somente o EPI definido para o risco e para o produto, ajuste-o corretamente, não improvise e comunique falta ou dano. EPI não substitui sinalização, procedimento nem higiene das mãos.

MENSAGEM FINAL
A proteção depende do equipamento adequado, em boas condições e usado corretamente.`;
 else if(/QUIMIC|SANEANTE|PRODUTO|DILUICAO|MISTUR/.test(t)) body=`RISCO E CENÁRIO
Saneantes incompatíveis, diluição incorreta e embalagens sem identificação podem causar queimaduras, irritação, intoxicação ou reação química.

OBJETIVO
Utilizar o produto correto da forma autorizada.

CONDUTA SEGURA
Confira rótulo, finalidade, diluição, tempo de contato e EPI indicado; consulte a FDS quando disponível; use recipientes identificados. Nunca misture produtos por iniciativa própria nem aumente a concentração para acelerar a limpeza. Em vazamento ou exposição, siga o procedimento local e comunique imediatamente.

MENSAGEM FINAL
Rótulo, FDS e procedimento orientam a utilização segura.`;
 else if(/RESIDU|LIXO|PERFURO|DESCARTE|RECICL/.test(t)) body=`RISCO E CENÁRIO
Resíduos podem conter objetos cortantes, material contaminado ou itens descartados de forma inadequada; compactar sacos ou introduzir a mão aumenta a exposição.

OBJETIVO
Recolher, segregar e destinar resíduos conforme o procedimento de cada área.

CONDUTA SEGURA
Observe sem pressionar o saco; nunca coloque a mão dentro de recipientes nem compacte resíduos manualmente. Use o recipiente e os EPIs previstos. Se encontrar objeto suspeito, perfurocortante ou material de origem desconhecida, não manipule: isole a área se for seguro e acione a liderança.

MENSAGEM FINAL
Segregação correta e coleta sem improviso protegem toda a equipe.`;
 else if(/QUEDA|ESCORREG|PISO MOLHADO|TROPECO/.test(t)) body=`RISCO E CENÁRIO
Piso molhado, resíduos, cabos e carrinhos mal posicionados podem provocar queda e bloquear a circulação de passageiros e equipes.

OBJETIVO
Controlar a área antes, durante e após a limpeza.

CONDUTA SEGURA
Sinalize e isole conforme o procedimento antes de molhar o piso; direcione a circulação sem bloquear saídas ou rotas acessíveis; retire obstáculos; use calçado indicado e só remova a sinalização quando a superfície estiver segura. Informe defeitos ou riscos que não consiga controlar.

MENSAGEM FINAL
Sinalize primeiro e libere a área somente quando estiver segura.`;
 else body=`RISCO E CENÁRIO
Na limpeza profissional em aeroporto, a equipe atua perto de passageiros, bagagens, carrinhos e fluxos contínuos. Falhas no tema “${topic}” podem causar exposição, queda, danos ou interrupção da operação.

OBJETIVO
Reforçar a prevenção e a aplicação do procedimento correto na área atendida.

CONDUTA SEGURA
Confirme o procedimento, inspecione a área e os recursos antes de iniciar; preserve saídas e rotas acessíveis; utilize somente materiais autorizados; sinalize os riscos e comunique situações inseguras à liderança. Se o risco não estiver controlado, interrompa a tarefa com segurança e peça orientação.

MENSAGEM FINAL
Siga o procedimento, proteja quem circula e não improvise diante de risco desconhecido.`;
 return body}
function analyzeCriticalContent(){
 const topic=$('topic')?.value.trim()||'',content=$('content')?.value.trim()||'',panel=$('critical-review');if(!panel)return;
 if(!topic||!content){panel.className='critical-review';panel.innerHTML='<b>Análise crítica do conteúdo</b><p>Informe o tema e escreva ou gere um rascunho. A verificação local não substitui avaliação da liderança ou do responsável técnico.</p>';return}
 const c=norm(content);
 const checks=[
  {label:'Risco ou consequência identificado',ok:/RISCO|PERIGO|EXPOSICAO|CONTAMINAC|QUEDA|CORTE|INTOXIC|ACIDENT|LESAO/.test(c)},
  {label:'Ação preventiva prática descrita',ok:/SINALIZ|UTILIZ|CONFIRA|VERIFIQUE|MANTENHA|INTERROMPA|NAO MISTURE|NUNCA|HIGIENIZ|SEPARE|RESPEITE|ISOL/.test(c)},
  {label:'Comunicação ou escalonamento previsto',ok:/COMUNIQUE|LIDERANCA|SUPERVIS|ACIONE|PROCURE|INTERROMPA|EQUIPE DE RESPOSTA|NAO RETOME/.test(c)},
  {label:'Contexto aeroportuário ou fluxo de pessoas',ok:/AEROPORT|TERMINAL|PASSAGEIRO|EMBARQUE|DESEMBARQUE|BAGAGEM|PORTAO|PATIO|CIRCULACAO|SANITARIO|AREA OPERACIONAL/.test(c)},
  {label:'Referência a procedimento, rótulo ou regra local',ok:/PROCEDIMENTO|ROTULO|FDS|FABRICANTE|POP|PLANO LOCAL|ORIENTACAO DA LIDERANCA/.test(c)}
 ];
 const passed=checks.filter(x=>x.ok).length;const level=passed>=4?'good':passed>=3?'review':'warning';
 const missing=checks.filter(x=>!x.ok).map(x=>x.label.toLowerCase());
 const concern=content.length>1050?'O texto pode ultrapassar o espaço físico do formulário institucional; gere e confira antes de imprimir.':'';
 panel.className=`critical-review ${level}`;
 panel.innerHTML=`<div class="critical-review-head"><b>Análise crítica do conteúdo</b><span>${passed}/5 critérios</span></div><p>${passed>=4?'Rascunho bem estruturado para revisão final.':passed>=3?'Rascunho parcial: melhore os pontos indicados antes de emitir.':'Conteúdo genérico ou incompleto: revise antes da emissão.'}</p><ul>${checks.map(c=>`<li class="${c.ok?'pass':'missing'}"><span>${c.ok?'✓':'!'}</span>${esc(c.label)}</li>`).join('')}</ul>${missing.length?`<p class="critical-review-note">Recomendação: inclua ${esc(missing.join('; '))}.</p>`:''}${concern?`<p class="critical-review-note">${concern}</p>`:''}<small>Verificação heurística offline; não é validação legal, pesquisa web nem aprovação técnica. Confirme POPs, rótulos/FDS, regras de acesso e plano de resposta do aeroporto.</small>`;
}
$('suggest-content').addEventListener('click',async()=>{const topic=$('topic').value.trim(),button=$('suggest-content'),sourcesBox=$('research-sources');if(!topic){$('topic').focus();showError('Informe o tema antes de pesquisar.');return}clearError();button.disabled=true;button.textContent='Pesquisando fontes confiáveis…';sourcesBox?.classList.add('hidden');if(sourcesBox)sourcesBox.innerHTML='';try{const result=await api('/api/research-topic',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:topic})});$('topic').value=result.title||topic;$('content').value=result.content||'';$('content').dispatchEvent(new Event('input'));if(sourcesBox){const sources=result.sources||[];sourcesBox.innerHTML=`<div class="research-sources-head"><b>Origem do conteúdo</b><span class="research-mode">${esc(({online:'Pesquisa na internet',cache:'Biblioteca local (cache)',offline:'Conteúdo offline salvo','offline-draft':'Rascunho offline não verificado'})[result.mode]||result.mode||'Pesquisa')}</span></div><p>${esc(result.message||'')}</p>${sources.length?'<ul>'+sources.map(src=>`<li><a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.title||src.url)}</a>${src.excerpt||src.snippet?`<small>${esc((src.excerpt||src.snippet).slice(0,420))}</small>`:''}</li>`).join('')+'</ul>':''}`;sourcesBox.classList.remove('hidden')}await loadTopics();toast(result.message||'Conteúdo carregado. Revise antes de emitir o DDS.',result.mode==='offline-draft'?'error':'success')}catch(err){showError(`Não foi possível pesquisar o tema: ${err.message}. Verifique a conexão e tente novamente.`);toast(err.message,'error')}finally{button.disabled=false;button.innerHTML='<span>⌕</span> Pesquisar na internet e gerar conteúdo'}});

// Bulk generation: combine selected topics, shifts and units into one reviewed PDF.
const bulkShiftBoxes=()=>[...document.querySelectorAll('input[name="bulk-shift"]')];
const bulkUnitBoxes=()=>[...document.querySelectorAll('input[name="bulk-unit"]')];
const bulkTopicBoxes=()=>[...document.querySelectorAll('input[name="bulk-topic"]')];
function renderBulkTopics(){const box=$('bulk-topic-list');if(!box)return;box.innerHTML=state.topics.length?state.topics.map(t=>`<label class="bulk-option"><input type="checkbox" name="bulk-topic" value="${t.id}" /> ${esc(t.title)}</label>`).join(''):'<p class="field-hint">Nenhum tema salvo na biblioteca. Será usado o tema atual.</p>';const all=$('bulk-all-topics');if(all)all.checked=false;bulkTopicBoxes().forEach(c=>c.addEventListener('change',()=>{if($('bulk-all-topics'))$('bulk-all-topics').checked=bulkTopicBoxes().length>0&&bulkTopicBoxes().every(x=>x.checked)}))}
$('bulk-all-shifts').addEventListener('change',e=>bulkShiftBoxes().forEach(c=>c.checked=e.target.checked));
$('bulk-all-units').addEventListener('change',e=>bulkUnitBoxes().forEach(c=>c.checked=e.target.checked));
$('bulk-all-topics').addEventListener('change',e=>bulkTopicBoxes().forEach(c=>c.checked=e.target.checked));
bulkShiftBoxes().forEach(c=>c.addEventListener('change',()=>{$('bulk-all-shifts').checked=bulkShiftBoxes().every(x=>x.checked)}));
bulkUnitBoxes().forEach(c=>c.addEventListener('change',()=>{$('bulk-all-units').checked=bulkUnitBoxes().every(x=>x.checked)}));
function closeBulkModal(){$('bulk-modal').classList.add('hidden')}
function openBulkModal(){
 clearError();
 if(!state.topics.length&&!$('topic').value.trim()){showError('Informe um tema ou cadastre temas na biblioteca antes de gerar DDS em massa.');$('topic').focus();return}
 if(!state.topics.length&&!$('content').value.trim()){showError('Pesquise ou escreva o conteúdo antes de gerar DDS em massa.');$('content').focus();return}
 if(!state.employees.length){showError('Importe a planilha de colaboradores antes de gerar DDS em massa.');return}
 renderBulkTopics();
 $('bulk-modal').classList.remove('hidden');
}
$('bulk-generate-open')?.addEventListener('click',openBulkModal);
$('bulk-generate-sidebar')?.addEventListener('click',openBulkModal);
$('bulk-close').addEventListener('click',closeBulkModal);$('bulk-cancel').addEventListener('click',closeBulkModal);
$('bulk-modal').addEventListener('click',e=>{if(e.target===$('bulk-modal'))closeBulkModal()});
$('bulk-generate-confirm').addEventListener('click',async()=>{
 const shifts=bulkShiftBoxes().filter(c=>c.checked).map(c=>c.value),units=bulkUnitBoxes().filter(c=>c.checked).map(c=>c.value);
 if(!shifts.length||!units.length){toast('Marque pelo menos um turno e uma unidade.','error');return}
 const selectedTopics=bulkTopicBoxes().filter(c=>c.checked).map(c=>state.topics.find(t=>String(t.id)===c.value)).filter(Boolean).map(t=>({topic:t.title,content:t.content}));
 const topics=selectedTopics.length?selectedTopics:[{topic:$('topic').value.trim(),content:$('content').value.trim()}];
 if(!topics.length||topics.some(t=>!t.topic||!t.content)){toast('Confira o título e o conteúdo de cada tema selecionado.','error');return}
 const payload={topic:topics[0].topic,content:topics[0].content,topics,date:$('dds-date').value,duration:'01:00',shifts,units};
 const button=$('bulk-generate-confirm');button.disabled=true;button.textContent='Gerando PDF único…';
 try{
  const result=await api('/api/generate-bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  closeBulkModal();
  const generated=result.generated||[];generated.forEach(item=>{if(item.metadata){state.history.unshift(item.metadata)}});state.history=state.history.slice(0,100);renderHistory();
  $('bulk-result-summary').textContent=`${result.count} arquivo PDF único gerado. Foram reunidos ${result.document_count||0} DDS de ${result.topics_count||topics.length} tema(s). ${result.skipped?.length||0} combinação(ões) sem colaboradores ignorada(s). ${result.failures?.length||0} falha(s).`;
  const rows=generated.map(item=>{const m=item.metadata;return `<div class="bulk-result-item"><div><b>PDF consolidado · ${esc(m.unit)}</b><small>${esc(m.topic)} · ${m.participants} participação(ões) contabilizadas · ${esc(m.file)}</small></div><a class="button secondary small" href="${esc(item.url)}" target="_blank" rel="noopener">Abrir PDF único</a></div>`});
  const skipped=(result.skipped||[]).map(x=>`<div class="bulk-result-note">Ignorado: ${esc(shiftLabel(x.shift))} · ${esc(x.unit)} — ${esc(x.reason)}</div>`);
  const failures=(result.failures||[]).map(x=>`<div class="bulk-result-note error">Falha: ${esc(shiftLabel(x.shift))} · ${esc(x.unit)} — ${esc(x.error)}</div>`);
  $('bulk-result-list').innerHTML=[...rows,...skipped,...failures].join('')||'<p>Nenhum PDF foi gerado.</p>';
  $('bulk-result-modal').classList.remove('hidden');
  toast(`Geração em massa concluída: ${result.count} PDF(s).`);
 }catch(err){closeBulkModal();showError(err.message);toast(err.message,'error')}
 finally{button.disabled=false;button.textContent='Gerar PDF único →'}
});
['bulk-result-close','bulk-result-done'].forEach(id=>$(id).addEventListener('click',()=>$('bulk-result-modal').classList.add('hidden')));
$('bulk-result-modal').addEventListener('click',e=>{if(e.target===$('bulk-result-modal'))$('bulk-result-modal').classList.add('hidden')});

$('dds-form').addEventListener('submit',ev=>{
 ev.preventDefault();clearError();
 const topic=$('topic').value.trim(),shift=$('shift').value,content=$('content').value.trim();
 if(!topic){showError('Informe o tema do DDS.');$('topic').focus();return}
 if(!shift){showError('Selecione o turno do DDS.');$('shift').focus();return}
 if(!content){showError('Gere uma sugestão de conteúdo ou escreva o conteúdo educativo antes de emitir o documento.');$('suggest-content').focus();return}
 const people=filteredEmployees();
 if(state.employees.length===0){showError('Importe a planilha de colaboradores antes de emitir o DDS. Isso evita gerar uma lista de presença incompleta.');return}
 if(!people.length){showError('Nenhum colaborador encontrado para a seleção. Confira turno, unidade e dados importados.');return}
 pendingGeneratePayload={topic,shift,work_site:$('operational-unit').value,unit:$('operational-unit').value,sector:'LIMPEZA',date:$('dds-date').value,duration:$('duration').value,content,employee_ids:$('use-all-shift-employees').checked?[]:people.map(person=>String(person.id))};
 const supervisors=state.supervisors[shift]||[];
 $('preflight-topic').textContent=topic;
 const rawDate=$('dds-date').value;
 $('preflight-date').textContent=`${rawDate?new Date(`${rawDate}T12:00:00`).toLocaleDateString('pt-BR'):'Data não informada'} · ${$('duration').value}`;
 $('preflight-shift').textContent=`${shiftLabel(shift)} (${shift})`;
 $('preflight-sector').textContent=unitLabel($('operational-unit').value);$('preflight-unit').textContent='GALEÃO';
 $('preflight-supervisors').textContent=supervisors.length?supervisors.join(' e '):'Responsável não identificado';
 $('preflight-count').textContent=`${people.length} participante${people.length===1?'':'s'}`;
 $('preflight-people').innerHTML=people.map((person,i)=>`<div class="preflight-person"><span>${i+1}</span><b>${esc(person.name)}</b><small>Unidade ${esc(person.unit||person.work_site||'Unidade não definida')} · Área LIMPEZA</small></div>`).join('');
 $('preflight-modal').classList.remove('hidden');
});
function closePreflight(){pendingGeneratePayload=null;$('preflight-modal').classList.add('hidden')}
$('cancel-preflight').addEventListener('click',closePreflight);
$('close-preflight').addEventListener('click',closePreflight);
$('preflight-modal').addEventListener('click',e=>{if(e.target===$('preflight-modal'))closePreflight()});
$('confirm-generate-btn').addEventListener('click',async()=>{
 if(!pendingGeneratePayload)return;
 const payload={...pendingGeneratePayload};pendingGeneratePayload=null;$('preflight-modal').classList.add('hidden');
 const button=$('generate-btn');button.disabled=true;button.innerHTML='<span>Preparando o documento…</span><span class="arrow">…</span>';
 try{
  const response=await api('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  lastPdfUrl=response.url;$('pdf-result-name').textContent=response.metadata.file;
  $('pdf-result-meta').textContent=`${response.metadata.template_pages||3} páginas · ${response.metadata.participants} participantes · turno ${shiftLabel(response.metadata.shift)}`;
  $('pdf-modal-summary').textContent=`${response.metadata.topic} · ${response.metadata.date}`;
  $('open-pdf-link').href=response.url;$('print-pdf-link').href=response.url;$('pdf-modal').classList.remove('hidden');
  if(response.metadata.warnings?.length)response.metadata.warnings.forEach(w=>toast(w,'error'));
  if(response.metadata.font_adjusted)toast('O texto foi ajustado para caber no espaço do formulário. Confira a legibilidade no PDF.','error');
  state.history.unshift(response.metadata);state.history=state.history.slice(0,100);renderHistory();
  toast('PDF gerado. Confira o documento antes de imprimir.');
 }catch(err){showError(err.message);toast(err.message,'error')}
 finally{button.disabled=false;button.innerHTML='<span>GERAR DDS</span><span class="arrow">→</span>'}
});
['close-pdf-modal','close-pdf-modal-2'].forEach(id=>$(id).addEventListener('click',()=>$('pdf-modal').classList.add('hidden')));$('pdf-modal').addEventListener('click',e=>{if(e.target===$('pdf-modal'))$('pdf-modal').classList.add('hidden')});document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('pdf-modal').classList.add('hidden');closePreflight();closeEmployeeEdit()}});

// Roster import and manual records
$('xlsx-file').addEventListener('change',async e=>{const file=e.target.files?.[0];if(file)await importRoster(file);e.target.value=''});
$('drop-zone').addEventListener('dragover',e=>{e.preventDefault();$('drop-zone').classList.add('dragover')});$('drop-zone').addEventListener('dragleave',()=> $('drop-zone').classList.remove('dragover'));$('drop-zone').addEventListener('drop',async e=>{e.preventDefault();$('drop-zone').classList.remove('dragover');const file=e.dataTransfer.files?.[0];if(file)await importRoster(file)});
async function importRoster(file){if(state.employees.length&&!confirm(`A relação atual contém ${state.employees.length} colaboradores. A nova planilha substituirá a relação atual; uma cópia de segurança será preparada antes da troca. Deseja continuar?`))return;const result=$('import-result');result.classList.remove('hidden','error');result.textContent='Lendo a planilha e identificando os turnos…';const fd=new FormData();fd.append('file',file);try{const r=await api('/api/import-roster',{method:'POST',body:fd});await loadState(false);const shifts=Object.entries(r.by_shift).map(([s,n])=>`${shiftLabel(s)}: ${n}`).join(' · ');result.innerHTML=`<b>Importação concluída.</b> ${r.count} colaboradores importados. Unidades válidas: GIG, TECA, UNITED e GATE GOURMET. Área/Setor: LIMPEZA. ${esc(shifts)}.${r.unknown_shift?` ${r.unknown_shift} sem turno identificado.`:''}${r.missing_site?` ${r.missing_site} sem unidade identificado; revise na lista de colaboradores.`:''}${r.work_sites?.length?` Locais encontrados: ${esc(r.work_sites.join(', '))}.`:''}${r.notes.length?`<br>${esc(r.notes.join(' '))}`:''}`;toast(`${r.count} colaboradores importados com sucesso.`);renderRoster()}catch(err){result.classList.add('error');result.textContent=err.message;toast(err.message,'error')}finally{result.classList.remove('hidden')}}
$('add-employee-toggle').addEventListener('click',()=>$('add-employee-form').classList.toggle('hidden'));
$('add-employee-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/employees',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('employee-name').value,shift:$('employee-shift').value,area:'LIMPEZA',sector:'LIMPEZA',work_site:$('employee-unit').value,unit:$('employee-unit').value,operational_unit:$('employee-unit').value})});$('add-employee-form').reset();$('employee-area').value='LIMPEZA';$('employee-sector').value='LIMPEZA';$('employee-unit').value='';$('add-employee-form').classList.add('hidden');await loadState(false);toast('Colaborador cadastrado na unidade selecionada.');}catch(err){toast(err.message,'error')}});
$('roster-search').addEventListener('input',renderRoster);$('roster-shift-filter').addEventListener('change',renderRoster);$('roster-sector-filter').addEventListener('change',renderRoster);$('roster-unit-filter').addEventListener('change',renderRoster);
async function deleteEmployee(id){if(!confirm('Remover este colaborador da relação de colaboradores?'))return;try{await api(`/api/employees/${encodeURIComponent(id)}`,{method:'DELETE'});state.employees=state.employees.filter(e=>e.id!==id);renderRoster();refreshSummary();toast('Colaborador removido.')}catch(err){toast(err.message,'error')}}
function renderRoster(){
 const all=state.employees;
 const sectorSelect=$('roster-sector-filter'),priorSector=sectorSelect.value;
 const sectors=[...new Set(all.map(e=>String(e.sector||'LIMPEZA').trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
 sectorSelect.innerHTML='<option value="TODOS">Todos os setores</option>'+sectors.map(sec=>`<option value="${esc(sec)}">${esc(sec)}</option>`).join('');
 sectorSelect.value=sectors.includes(priorSector)?priorSector:'TODOS';
 const unitSelect=$('roster-unit-filter'),priorUnit=unitSelect.value;
 unitSelect.innerHTML='<option value="TODOS">Todas as unidades</option><option value="SEM_UNIDADE">Local não definido</option>'+(state.work_sites||state.units||[]).map(site=>`<option value="${esc(site)}">${esc(site)}</option>`).join('');
 unitSelect.value=(priorUnit==='SEM_UNIDADE'||(state.work_sites||state.units||[]).includes(priorUnit))?priorUnit:'TODOS';
 const q=norm($('roster-search').value),f=$('roster-shift-filter').value,sf=sectorSelect.value,uf=unitSelect.value;
 const list=all.filter(e=>(!q||norm(e.name).includes(q))&&(f==='TODOS'||(f==='SEM_TURNO'&&!e.shift)||e.shift===f)&&(sf==='TODOS'||norm(e.sector||'LIMPEZA')===norm(sf))&&(uf==='TODOS'||(uf==='SEM_UNIDADE'&&!e.work_site)||norm(e.work_site)===norm(uf)));
 const perShift=['DIURNO','VESPERTINO','NOTURNO'].map(sh=>`${shiftLabel(sh)}: ${all.filter(e=>e.shift===sh).length}`).join(' · ');
 const unitCounts=(state.work_sites||state.units||[]).map(site=>`${site}: ${all.filter(e=>norm(e.work_site)===norm(site)).length}`).join(' · ');
 $('roster-subtitle').textContent=`${all.length} colaboradores cadastrados · Área/Setor LIMPEZA · ${perShift}. ${unitCounts}`;
 $('roster-count-footer').textContent=`Exibindo ${list.length} de ${all.length} registros`;
 if(!list.length){$('roster-body').innerHTML=`<tr><td colspan="7" class="empty-state">${all.length?'Nenhum resultado para os filtros selecionados.':'Importe uma planilha para carregar a relação de colaboradores.'}</td></tr>`;return}
 $('roster-body').innerHTML=list.slice(0,1000).map(e=>`<tr><td class="person-cell"><span class="person-initial">${esc((e.name||'?').split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase())}</span>${esc(e.name)}</td><td><span class="shift-pill ${(e.shift||'').toLowerCase()}">${esc(shiftLabel(e.shift))}</span></td><td>${esc(e.area||'—')}</td><td>${esc(e.sector||'LIMPEZA')}</td><td>${esc(e.unit||e.operational_unit||'Unidade não definida')}</td><td>${esc(e.work_site||'Unidade não definida')}</td><td><button class="edit-button" data-edit="${esc(e.id)}">Editar</button> <button class="delete-button" data-delete="${esc(e.id)}">Remover</button></td></tr>`).join('');
 $('roster-body').querySelectorAll('[data-delete]').forEach(b=>b.addEventListener('click',()=>deleteEmployee(b.dataset.delete)));
 $('roster-body').querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>editEmployee(b.dataset.edit)));
}
function editEmployee(id){
 const e=state.employees.find(x=>x.id===id);if(!e)return;
 editingEmployeeId=id;
 $('edit-employee-id').value=id;$('edit-employee-name').value=e.name||'';
 $('edit-employee-shift').value=['DIURNO','VESPERTINO','NOTURNO'].includes(e.shift)?e.shift:'DIURNO';
 $('edit-employee-area').value=e.area||'LIMPEZA';$('edit-employee-sector').value='LIMPEZA';$('edit-employee-unit').value=e.work_site||'';
 $('employee-edit-modal').classList.remove('hidden');$('edit-employee-name').focus();
}
function closeEmployeeEdit(){const modal=$('employee-edit-modal');if(modal)modal.classList.add('hidden');editingEmployeeId=null}
$('close-employee-edit').addEventListener('click',closeEmployeeEdit);$('cancel-employee-edit').addEventListener('click',closeEmployeeEdit);
$('employee-edit-modal').addEventListener('click',e=>{if(e.target===$('employee-edit-modal'))closeEmployeeEdit()});
$('employee-edit-form').addEventListener('submit',async e=>{
 e.preventDefault();if(!editingEmployeeId)return;
 const id=editingEmployeeId;const body={name:$('edit-employee-name').value.trim(),shift:$('edit-employee-shift').value,area:$('edit-employee-area').value.trim(),sector:'LIMPEZA',unit:$('edit-employee-unit').value,operational_unit:$('edit-employee-unit').value,work_site:$('edit-employee-unit').value};
 const save=$('save-employee-edit');save.disabled=true;save.textContent='Salvando…';
 try{await api(`/api/employees/${encodeURIComponent(id)}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});closeEmployeeEdit();await loadState(false);toast('Cadastro atualizado: nome, turno e unidade. Área e setor LIMPEZA mantidos.')}catch(err){toast(err.message,'error')}
 finally{save.disabled=false;save.textContent='Salvar alterações'}
});

function renderHistory(){const all=state.history||[];const q=norm($('history-search')?.value||'');const shift=$('history-shift-filter')?.value||'TODOS';const list=all.filter(h=>(!q||norm(`${h.topic||''} ${h.unit||''} ${h.work_site||''} ${h.sector||''} ${h.date||''} ${h.file||''}`).includes(q))&&(shift==='TODOS'||h.shift===shift));if(!list.length){$('history-body').innerHTML=`<tr><td colspan="6" class="empty-state">${all.length?'Nenhuma emissão corresponde aos filtros.':'Nenhum DDS gerado ainda.'}</td></tr>`;return}$('history-body').innerHTML=list.map(h=>`<tr><td>${esc(h.date)}</td><td><b>${esc(h.topic)}</b></td><td><span class="shift-pill ${(h.shift||'').toLowerCase()}">${esc(shiftLabel(h.shift))}</span></td><td>${esc(h.unit||'GIG')} · ${esc(h.work_site||'Todas as unidades')}</td><td>${esc(h.participants)}</td><td><a class="history-link" href="/api/files/${encodeURIComponent(h.file)}" target="_blank" rel="noopener">Abrir PDF ↗</a></td></tr>`).join('')}
$('history-search')?.addEventListener('input',renderHistory);$('history-shift-filter')?.addEventListener('change',renderHistory);
$('backup-download').addEventListener('click',async()=>{
 const button=$('backup-download'),result=$('backup-result');button.disabled=true;button.textContent='Preparando cópia…';
 try{const backup=await api('/api/backup-data');const blob=new Blob([JSON.stringify(backup,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`gerador_dds_backup_${dayISO()}.json`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);result.classList.remove('hidden','error');result.textContent=`Cópia criada com ${backup.state.employees.length} colaboradores, ${backup.state.history.length} registros de histórico e ${backup.custom_topics.length} temas personalizados. Guarde o arquivo em outro local.`;toast('Cópia de segurança baixada.')}catch(err){result.classList.remove('hidden');result.classList.add('error');result.textContent=err.message;toast(err.message,'error')}
 finally{button.disabled=false;button.textContent='Baixar cópia de segurança'}
});
$('backup-restore-trigger').addEventListener('click',()=>$('backup-file').click());
$('backup-file').addEventListener('change',async e=>{
 const file=e.target.files?.[0];e.target.value='';if(!file)return;
 const result=$('backup-result');
 try{const backup=JSON.parse(await file.text());if(backup.format!=='gerador-dds-backup')throw new Error('Este arquivo não parece ser uma cópia de segurança do Gerador de DDS.');
  const employeeCount=Array.isArray(backup.state?.employees)?backup.state.employees.length:0;
  if(!confirm(`ATENÇÃO: a restauração substituirá os dados atuais por uma cópia com ${employeeCount} colaboradores. Os dados atuais terão uma cópia de segurança antes da troca. Continuar?`))return;
  result.classList.remove('hidden','error');result.textContent='Validando e restaurando a cópia de segurança…';
  const response=await api('/api/restore-backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(backup)});
  await loadState(false);await loadTopics();renderRoster();result.classList.remove('error');result.classList.remove('hidden');result.textContent=`Restauração concluída: ${response.employees} colaboradores, ${response.history} registros de histórico e ${response.custom_topics} temas personalizados. ${response.message}`;toast('Cópia de segurança restaurada com sucesso.')
 }catch(err){result.classList.remove('hidden');result.classList.add('error');result.textContent=err.message;toast(err.message,'error')}
});

function renderConfig(){const shiftOrder=['DIURNO','VESPERTINO','NOTURNO'];$('supervisor-table').innerHTML=shiftOrder.map(s=>`<div class="supervisor-row"><b>${esc(shiftLabel(s))}</b><span>${esc((state.supervisors[s]||[]).join(' e '))}</span></div>`).join('');$('unit-grid').innerHTML=(state.work_sites||state.units||['GIG','TECA','UNITED','GATE GOURMET']).map((u,i)=>`<div class="unit-chip"><span>${i+1}</span>${esc(u)}</div>`).join('');const unitSupervisors=[['UNITED','WILKER MARIA'],['GIG · MANHÃ','ORLANDO BARBOSA / EVELYN MONTOVANI'],['GIG · TARDE','VICTOR HUGO / BRUNA FRAGA / KAYO LESSA'],['GIG · NOITE','MARIA DO SOCORRO'],['TECA · Terminal de Carga','PEDRO PAULO'],['GATE GOURMET','MARCIA FASUTINO']];const target=$('unit-supervisor-table');if(target)target.innerHTML=unitSupervisors.map(([unit,name])=>`<div class="supervisor-row"><b>${esc(unit)}</b><span>${esc(name)}</span></div>`).join('')}

async function updateEnvironmentStatus(){
 const tag=$('environment-status');if(!tag)return;
 const paint=(online,label)=>{
  tag.classList.toggle('online',online);tag.classList.toggle('offline',!online);
  tag.innerHTML='<span class="live-dot" aria-hidden="true"></span><span>'+label+'</span>';
 };
 if(navigator.onLine===false){paint(false,'Ambiente Offline Ativado');return}
 try{
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),4500);
  try{
   const response=await fetch('/api/health',{method:'GET',cache:'no-store',headers:{'Accept':'application/json'},signal:controller.signal});
   clearTimeout(timeout);
   if(!response.ok)throw new Error('Servidor indisponível');
   paint(true,'Ambiente Online Ativado');
  }finally{clearTimeout(timeout)}
 }catch(_){paint(false,'Ambiente Offline Ativado')}
}
window.addEventListener('online',updateEnvironmentStatus);
window.addEventListener('offline',updateEnvironmentStatus);
function renderStorageStatus(info){
 const card=$('sidebar-status');if(!card||!info)return;
 const text=card.querySelector('small'),icon=card.querySelector('.nav-icon');
 if(text)text.textContent=info.storage_label||'Estado do armazenamento não confirmado.';
 card.classList.toggle('storage-warning',!info.persistent_storage_configured);
 card.setAttribute('aria-label',info.persistent_storage_configured?'Dados da aplicação salvos':'Cópia de segurança deste navegador');
 if(icon)icon.textContent=info.persistent_storage_configured?'✓':'↻';
}
async function loadState(showToast=false){
 try{
  let r=await api('/api/state');
  if(!r.employees?.length){
   const backup=readBrowserBackup();
   if(backup?.state?.employees?.length){
    const response=await fetch('/api/restore-backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(backup)});
    let body={};try{body=await response.json()}catch{}
    if(response.ok){
     r=await api('/api/state');
     toast(`Lista recuperada automaticamente da cópia deste navegador: ${body.employees} colaboradores.`);
    }else{
     toast(`Há uma cópia de segurança neste navegador, mas a recuperação falhou: ${body.error||response.statusText}`,'error');
    }
   }
  }
  Object.assign(state,r);renderStorageStatus(r);renderEmployeeSelection();refreshSummary();renderRoster();renderHistory();renderConfig();
  if(state.employees.length)void refreshBrowserBackup();
  if(showToast)toast('Dados atualizados.');
 }catch(err){toast(`Não foi possível carregar a plataforma: ${err.message}`,'error')}
}
async function initializeApp(){updateEnvironmentStatus();await loadState();await loadTopics()}
initializeApp();
