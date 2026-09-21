<script lang="ts">
  import { onMount } from 'svelte';
  import { localDraft, encodeDraft, decodeDraft, type Draft } from './drafts';
  import { unzipSync } from 'fflate';
  import Metrics from './Metrics.svelte';
  import TaskMetrics from './TaskMetrics.svelte';
  import TaskFormat from './TaskFormat.svelte';
  import ColumnHelp from './ColumnHelp.svelte';
  import SubmissionFormat from './SubmissionFormat.svelte';
  type Row = Record<string, any>;
  const taskNames: Record<string,string> = {alignment:'Alignment',segmentation:'Waqf segmentation',timing:'Word timing'};
  let task='alignment', selectedTasks=['alignment'];
  $: profileKey=task+':'+hardwareClass;
  const tabs = ['Leaderboard', 'Dataset', 'Metrics', 'Submit'];
  let tab = 'Leaderboard', corpus: Row | null = null, rows: Row[] = [], loading = true, error = '';
  let boardBusy = false, theme = document.documentElement.dataset.theme || 'light';
  let filters: Record<string, string[]> = {}, extra = false, sortKey = 'rank', ascending = true;
  let datasetSort = 'id', datasetAsc = true, search = '', signedIn = false, oauthAvailable = false;
  let detail: Row | null = null, dialog: HTMLDialogElement, sequence = 0;
  let batchPreview: Row | null = null;
  let uploaded: Record<string, File> = {}, uploadIssues: string[] = [], preview: Row | null = null;
  $: preview = batchPreview?.tasks?.[task] || null;
  let profileDrafts: Record<string,{files:Record<string,File>;hardware:string}> = {};
  let busy = false, publishBusy = false, notice = '', consent = false, confirmDialog: HTMLDialogElement;
  let system = '', description = '', url = '', email = '', userParameters = 0, hardwareClass = 'cpu', hardware = '';
  let sessionReady = false;
  let draftReady = false, draftStatus = '', signInBusy = false;
  let draftSave: Promise<unknown> = Promise.resolve();
  let form: HTMLFormElement, previewSeq = 0, validationTimer: ReturnType<typeof setTimeout>;
  const facets = [{key:'id', label:'Recording'}, {key:'reciter', label:'Reciter'}, {key:'style', label:'Style'}, {key:'content', label:'Content'}, {key:'noisy', label:'Recording acoustics'}, {key:'multi_surah', label:'Surahs'}, {key:'riwayah', label:'Riwayah'}];
  const columnHelp: Record<string, string> = {
    rank: 'Position by the primary score for the selected recordings. Lower is better; equal scores share a rank.',
    segments_f1: 'Balances finding complete recitation segments between waqf stops with avoiding extra or incorrectly split segments. Higher is better.',
    boundaries_f1: 'Balances finding the starts and ends of recitation segments between waqf stops with avoiding extra boundaries. Higher is better.',
    segments_found: 'The share of reference recitation segments returned with usable boundaries. Higher means fewer missed segments.',
    segments_correct: 'The share of evaluated output segments that match a complete reference segment. Higher means fewer unusable segments.',
    boundaries_found: 'The share of reference starts and ends found within the accepted boundary windows. Higher is better.',
    boundaries_correct: 'The share of evaluated starts and ends that match reference boundaries. Higher is better.',
    words_timed: 'The percentage of supplied words whose start and end are both within 300 ms of the reference. Higher is better.',
    clean_clips: 'The percentage of clips where every supplied word has its start and end within 300 ms of the reference. Higher means more clips usable without timing corrections.',
    system: 'The submitted system and its CPU or GPU run. Select its name to see its description, links, and hardware.',
    words_f1: 'Balances finding the recited words with getting its word claims right. Higher means more complete, more accurate alignment.',
    clean_segments: 'The percentage of Quran segments with all the right words and usable audio boundaries. Higher means more segments ready to use as supplied.',
    repeats_f1: 'Balances catching real repetitions with avoiding false repeat detections. Higher is better.',
    words_per_segment: 'Average Quran words per output segment. Lower means finer chunks; higher means longer passages. Neither is inherently better.',
    rtf: 'Audio processed per unit of time. 10× means ten minutes of audio were processed in one minute. Higher is faster. The exact self-reported real-time factor (RTF) and hardware appear below each value.',
    trusted_coverage: 'Useful green output after each incorrect green segment cancels four correct green segments. Higher is better.',
    unsafe_green: 'The share of green segments, confidence 0.80 or higher, that are not clean. Lower is safer.',
    words_found: 'The percentage of recited words found in the right audio segments. Higher means fewer words missed.',
    words_correct: 'The percentage of claimed words actually recited in their audio segments. Higher means fewer incorrect word claims.',
    repeats_caught: 'The percentage of real repetition events detected. Higher means fewer repetitions missed.',
    repeats_real: 'The percentage of detected repetition events that really happened. Higher means fewer false repeat detections.',
    user_parameters: 'Self-reported count of result-affecting controls available to users, such as demo settings or documented package options. Excludes internal developer settings. Fewer can be simpler; more can offer flexibility.',
  };
  const alignmentCols = [{key:'words_f1', label:'Words F1'}, {key:'clean_segments', label:'Clean segments'}, {key:'repeats_f1', label:'Repeats F1'}, {key:'words_per_segment', label:'Words / segment'}, {key:'rtf', label:'Processing speed'}, {key:'trusted_coverage', label:'Trusted coverage'}, {key:'unsafe_green', label:'Unsafe green'}];
  const extraCols = [{key:'words_found', label:'Words found'}, {key:'words_correct', label:'Words correct'}, {key:'repeats_caught', label:'Repeats caught'}, {key:'repeats_real', label:'Repeats real'}];
  $: baseCols = task==='alignment'?alignmentCols:task==='segmentation'?[{key:'segments_f1',label:'Segments F1'},{key:'boundaries_f1',label:'Boundaries F1'},{key:'rtf',label:'Processing speed'}]:[{key:'words_timed',label:'Words timed correctly'},{key:'clean_clips',label:'Clean clips'}];
  $: if(draftReady) saveDraft({metadata:{system,description,url,email,user_parameters:userParameters,hardware_class:hardwareClass,hardware,tasks:[task],selected_tasks:selectedTasks},profiles:{...profileDrafts,[profileKey]:{files:uploaded,hardware}},corpus:corpus?.latest||''});
  function snapshot(): Draft {return {metadata:{...metadata(),selected_tasks:selectedTasks},profiles:{...profileDrafts,[profileKey]:{files:uploaded,hardware}},corpus:corpus?.latest||''};}
  function saveDraft(draft: Draft) {
    draftSave = draftSave.catch(()=>{}).then(()=>localDraft(draft)).then(()=>{draftStatus='Draft saved on this device.';}).catch(()=>{draftStatus='Browser draft saving is unavailable. Keep this tab open; sign-in will save a private handoff first.';});
  }
  async function restoreDraft() {
    const params=new URLSearchParams(location.hash.split('?')[1]||'');
    const token=params.get('resume');
    let draft: Draft | undefined;
    try {
      draft=token?decodeDraft(await api('/api/draft-resume',{method:'POST',headers:{'x-qab-request':'1','Content-Type':'application/json'},body:JSON.stringify({token})})):await localDraft();
    } catch(e) {if(token){notice=String(e);try{draft=await localDraft();}catch{}}}
    if(draft) {
      const m=draft.metadata;
      system=m.system||'';description=m.description||'';url=m.url||'';email=m.email||'';userParameters=m.user_parameters??0;
      hardwareClass=m.hardware_class==='gpu'?'gpu':'cpu';task=m.tasks?.[0] in taskNames?m.tasks[0]:'alignment';selectedTasks=Object.values(draft.profiles).some(p=>Object.keys(p.files).length)?(m.selected_tasks||[task]):['alignment'];if(!selectedTasks.includes(task))task=selectedTasks[0];profileDrafts=Object.fromEntries(Object.entries(draft.profiles).map(([k,v])=>[k.includes(':')?k:'alignment:'+k,v]));const key=task+':'+hardwareClass;uploaded=profileDrafts[key]?.files||{};hardware=profileDrafts[key]?.hardware||'';await loadBoard();
      consent=false;
      if(token)history.replaceState(null,'','#submit');
      if(hasFiles()){await validate();draftStatus='Draft restored. Files and scores are ready to review.';}
    }
    if(params.has('auth_error'))notice='Sign-in was cancelled or could not be verified. Your draft is here; you can try again.';
    draftReady=true;
  }
  async function signIn() {
    const popup=window.open('about:blank','_blank');
    if(popup)popup.opener=null;
    signInBusy=true;notice='';
    try {
      await draftSave;
      const saved=await api('/api/draft-handoff',{method:'POST',headers:{'x-qab-request':'1','Content-Type':'application/json'},body:JSON.stringify(await encodeDraft(snapshot()))});
      const destination='/auth/login?resume='+encodeURIComponent(saved.token);
      if(popup)popup.location.href=destination;else window.location.assign(destination);
    } catch(e) {popup?.close();notice='Could not save your sign-in handoff. Your files and scores are still here. '+String(e);}
    finally{signInBusy=false;}
  }
  $: cols = !extra||task==='timing'?baseCols:task==='alignment'?[...baseCols.slice(0,1),...extraCols.slice(0,2),baseCols[1],baseCols[2],...extraCols.slice(2),...baseCols.slice(3)]:[...baseCols,...['segments','boundaries'].flatMap(k=>[{key:k+'_found',label:(k==='segments'?'Segments':'Boundaries')+' found'},{key:k+'_correct',label:(k==='segments'?'Segments':'Boundaries')+' correct'}])];
  $: recordings = (corpus?.recordings || []) as Row[];
  $: selected = recordings.filter(r => matches(r, filters));
  $: visibleRows = (boardBusy?[]:[...rows]).filter(r => (r.display_name||r.system).toLowerCase().includes(search.toLowerCase())).sort((a,b) => compare(value(a,sortKey),value(b,sortKey),ascending));
  $: datasetRows = [...selected].sort((a,b) => compare(a[datasetSort], b[datasetSort], datasetAsc));
  $: totalMinutes = selected.reduce((s,r) => s+r.duration_s/60,0);
  function compare(a: any,b: any,asc: boolean) { if(a==null)return b==null?0:1; if(b==null)return -1; return (typeof a==='number'?a-b:String(a).localeCompare(String(b)))*(asc?1:-1); }
  function value(r: Row,k: string) {if(k==='system')return r.display_name||r.system;if(k==='rtf')return r.scores.rtf==null?null:1/r.scores.rtf;return k in r?r[k]:r.scores[k];}
  function matches(r: Row, active: Record<string,string[]>, except = '') {return Object.entries(active).every(([k,v]) => k===except || !v.length || v.includes(String(r[k])));}
  function options(key: string, active: Record<string,string[]>): string[] {return [...new Set(recordings.filter(r=>matches(r,active,key)).map(r=>String(r[key])))].sort();}
  function toggleFilter(k:string,v:string) {const available=options(k,filters);const binary=available.length===2;const current=filters[k]?.length?filters[k]:(binary?available:[]);if(binary&&current.length===1&&current.includes(v))return;filters={...filters,[k]:!v?[]:current.includes(v)?current.filter(x=>x!==v):[...current,v]};loadBoard();}
  function toggleTheme() {theme=theme==='light'?'dark':'light';document.documentElement.dataset.theme=theme;try{localStorage.setItem('qab-theme',theme);}catch{}}
  function label(k: string,v: string) {if(k==='noisy') return v==='true'?'Noisy':'Clean'; if(k==='multi_surah')return v==='true'?'Multiple surahs':'Single surah'; return ({hafs_an_asim:'Hafs',quran_only:'Quran only',prayer:'Prayer',hadr:'Hadr',murattal:'Murattal',mujawwad:'Mujawwad',muallim:'Muallim'} as Record<string,string>)[v] || v;}
  function fmtSpeed(rtf: number) {const speed=1/rtf;return `${speed.toFixed(speed>=10?1:2)}× realtime`;}
  function fmt(k: string,v: any) {if(v==null)return '—'; if(k==='words_per_segment')return v.toFixed(1); if(k==='rtf')return fmtSpeed(v); if(k==='unsafe_green')return `${(v*100).toFixed(2)}%`; return `${(v*100).toFixed(1)}%`;}
  function submittedAt(value: string) {return new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value));}
  function sort(k: string) {ascending=sortKey===k?!ascending:k==='rank'||k==='system'||k==='user_parameters'||k==='unsafe_green';sortKey=k;}
  function sortDataset(k: string) {datasetAsc=datasetSort===k?!datasetAsc:true;datasetSort=k;}
  async function api(path: string, init?: RequestInit) {const r=await fetch(path,init);const j=await r.json();if(!r.ok)throw new Error(typeof j.detail==='string'?j.detail:'Request could not be completed');return j;}
  async function load(version?: string) {loading=true;error='';try{corpus=await api('/api/corpus'+(version?'?version='+encodeURIComponent(version):''));filters={};await loadBoard();}catch(e){error=String(e);}finally{loading=false;}}
  async function loadBoard() {const n=++sequence;if(!corpus)return;boardBusy=true;const ids=(corpus.recordings as Row[]).filter(r=>matches(r,filters)).map(r=>r.id);if(!ids.length){rows=[];boardBusy=false;return;}const q=new URLSearchParams({version:corpus.version,task});ids.forEach(id=>q.append('ids',id));try{const data=await api('/api/leaderboard?'+q);if(n===sequence)rows=data.rows;}catch(e){if(n===sequence)error=String(e);}finally{if(n===sequence)boardBusy=false;}}
  async function changeFilter(k: string,v: string) {filters={...filters,[k]:v?[v]:[]};await loadBoard();}
  function selectTab(t: string) {if(t==='Submit'&&!selectedTasks.includes(task))switchTask(selectedTasks[0]);tab=t;history.replaceState(null,'','#'+t.toLowerCase());}
  function inspect(r: Row) {detail=r;dialog.showModal();}
  function rankRecording(id: string) {filters={id:[id]};selectTab('Leaderboard');loadBoard();}
  function invalidate() {batchPreview=null;consent=false;notice='';busy=false;previewSeq++;clearTimeout(validationTimer);if(hasFiles())validationTimer=setTimeout(validate,700);}
  function switchProfile(next:string) {if(next===hardwareClass)return;profileDrafts[task+':'+hardwareClass]={files:uploaded,hardware};hardwareClass=next;uploaded=profileDrafts[task+':'+next]?.files||{};hardware=profileDrafts[task+':'+next]?.hardware||'';invalidate();}
  function switchTask(next:string) {if(next===task)return;profileDrafts[task+':'+hardwareClass]={files:uploaded,hardware};task=next;uploaded=profileDrafts[task+':'+hardwareClass]?.files||{};sortKey='rank';ascending=true;invalidate();loadBoard();}
  function toggleTask(next:string) {if(selectedTasks.includes(next)){if(selectedTasks.length===1)return;selectedTasks=selectedTasks.filter(t=>t!==next);if(task===next)switchTask(selectedTasks[0]);else invalidate();}else{selectedTasks=[...selectedTasks,next];switchTask(next);}}
  function downloadReport(key:string){const blob=new Blob([JSON.stringify(batchPreview?.tasks[key]?.report,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=key+'-report.json';a.click();URL.revokeObjectURL(url);}
  function metadata() {return {system,description,url,email,user_parameters:Number(userParameters),hardware_class:hardwareClass||null,hardware,tasks:[task]};}
  function taskFiles(key:string) {return key===task?uploaded:profileDrafts[key+':'+hardwareClass]?.files||{};}
  function hasFiles() {return selectedTasks.some(key=>Object.keys(taskFiles(key)).length>0);}
  function body() {const b=new FormData();for(const key of selectedTasks)Object.values(taskFiles(key)).forEach(f=>b.append('files',f,key+'/'+f.name));b.append('metadata',JSON.stringify({...metadata(),tasks:selectedTasks}));return b;}
  async function addFiles(files: FileList | File[]) {invalidate();uploadIssues=[];try{const additions: Record<string, File>={};const routed: Record<string,Record<string,File>>={};let size=0;
    for(const file of Array.from(files)){size+=file.size;if(size>20*1024*1024)throw new Error('Upload limit is 20 MB.');
      if(file.name.toLowerCase().endsWith('.zip')){const bytes=new Uint8Array(await file.arrayBuffer());let expanded=0;const entries=unzipSync(bytes,{filter:f=>{expanded+=f.originalSize;if(expanded>20*1024*1024)throw new Error('Expanded ZIP exceeds 20 MB.');return !f.name.endsWith('/');}});for(const [path,data] of Object.entries(entries)){const name=path.split('/').pop()!;if(path.includes('..')||!name.endsWith('.json'))throw new Error('ZIP must contain only prediction JSON files.');const target=path.split('/').find(part=>part in taskNames)||task;const dest=target===task?additions:(routed[target]||={});if(dest[name])throw new Error(`Duplicate ${name} for ${target} in upload.`);dest[name]=new File([new Uint8Array(data)],name,{type:'application/json'});}}
      else {if(!file.name.endsWith('.json'))throw new Error('Choose JSON files or a ZIP.');if(additions[file.name])throw new Error(`Duplicate ${file.name} in upload.`);additions[file.name]=file;}}
    const merged={...uploaded,...additions};if(Object.values(merged).reduce((n,f)=>n+f.size,0)>20*1024*1024)throw new Error('Total upload limit is 20 MB.');uploaded=merged;for(const [target,files] of Object.entries(routed)){const key=target+':'+hardwareClass;profileDrafts[key]={files:{...profileDrafts[key]?.files,...files},hardware:profileDrafts[key]?.hardware||hardware};if(!selectedTasks.includes(target))selectedTasks=[...selectedTasks,target];}profileDrafts={...profileDrafts};await validate();
  }catch(e){uploadIssues=[String(e)];}}
  async function validate() {clearTimeout(validationTimer);if(!hasFiles())return;const n=++previewSeq;busy=true;notice='';try{const data=await api('/api/preview-batch',{method:'POST',headers:{'x-qab-request':'1'},body:body()});if(n===previewSeq)batchPreview=data;}catch(e){if(n===previewSeq)notice=String(e);}finally{if(n===previewSeq)busy=false;}}
  function removeFile(name: string) {const next={...uploaded};delete next[name];uploaded=next;invalidate();validate();}
  function review() {if(!form.reportValidity())return;if(batchPreview?.complete)confirmDialog.showModal();}
  async function publish() {if(!batchPreview?.token||!consent)return;publishBusy=true;notice='';const b=body();b.append('preview_token',batchPreview.token);b.append('confirmed','true');try{const result=await api('/api/publish-batch',{method:'POST',headers:{'x-qab-request':'1'},body:b});confirmDialog.close();batchPreview=null;for(const key of result.tasks)profileDrafts[key+':'+hardwareClass]={files:{},hardware};uploaded={};profileDrafts={...profileDrafts};consent=false;selectedTasks=['alignment'];switchTask('alignment');notice='Published '+result.tasks.map((key:string)=>taskNames[key]).join(', ')+'.';await loadBoard();}catch(e){notice=String(e);}finally{publishBusy=false;}}
  async function refreshSession(){try{const s=await api('/api/session');const changed=!signedIn&&s.signed_in;signedIn=s.signed_in;oauthAvailable=s.oauth_available;if(changed&&draftReady&&Object.keys(uploaded).length)await validate();}catch{}finally{sessionReady=true;}}
  onMount(()=>{const media=matchMedia('(prefers-color-scheme: dark)');const deviceTheme=()=>{try{if(localStorage.getItem('qab-theme'))return;}catch{}theme=media.matches?'dark':'light';document.documentElement.dataset.theme=theme;};media.addEventListener('change',deviceTheme);const t=tabs.find(t=>location.hash.split('?')[0]==='#'+t.toLowerCase());if(t)tab=t;Promise.all([load(),refreshSession()]).then(restoreDraft);window.addEventListener('focus',refreshSession);return()=>{window.removeEventListener('focus',refreshSession);media.removeEventListener('change',deviceTheme);};});
</script>

<svelte:head><title>{tab} · Quran Alignment Benchmark</title></svelte:head>
<header class="site-header"><a class="brand" href="#leaderboard" onclick={()=>selectTab('Leaderboard')}><span class="brand-icon" aria-hidden="true">≋</span><span>Quran Alignment <span class="brand-secondary">Benchmark</span></span></a>
<nav aria-label="Main navigation">{#each tabs as t}<button class:active={tab===t} aria-current={tab===t?'page':undefined} onclick={()=>selectTab(t)}>{t}</button>{/each}</nav>
<div class="header-links"><a href="https://huggingface.co/datasets/hetchyy/quran-alignment-benchmark" target="_blank" rel="noreferrer">Dataset ↗</a><a href="https://github.com/Hetchy/quran-alignment-benchmark" target="_blank" rel="noreferrer">Repo ↗</a><button class="theme-button" aria-label={'Switch to '+(theme==='light'?'dark':'light')+' mode'} onclick={toggleTheme}>{theme==='light'?'☾ Dark':'☀ Light'}</button></div></header>
<main>
{#if error}<div role="alert" class="alert">{error} <button onclick={()=>load()}>Retry</button></div>{/if}
{#if tab!=='Submit'&&tab!=='Dataset'}<fieldset class="segmented task-switcher"><legend>Task</legend><div>{#each Object.entries(taskNames) as [key,name]}<button class:chosen={task===key} aria-pressed={task===key} onclick={()=>switchTask(key)}>{name}</button>{/each}</div></fieldset>{/if}
{#if loading}<div class="skeleton" aria-live="polite">Loading benchmark…</div>
{:else if tab==='Leaderboard'||tab==='Dataset'}
  <div class="section-heading"><div><h1>{tab==='Leaderboard'?'Compare '+taskNames[task].toLowerCase()+' systems':'Explore the recordings'}</h1><p>{tab==='Leaderboard'?(task==='segmentation'?'Split full recordings at the reciter’s audible stops (waqf). No text matching required.':'Results on Quran recitation audio. Choose recordings that matter to you.'):'Browse the corpus and compare systems on individual recordings.'}</p></div><label class="version">Corpus<select aria-label="Corpus version" value={corpus?.version} onchange={e=>load(e.currentTarget.value)}>{#each corpus?.versions||[] as v}<option value={v}>{v}{v===corpus?.latest?' · Latest':''}</option>{/each}</select></label></div>
  {#if tab==='Dataset'}<div class="corpus-help"><a href="https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=corpus-issue.yml" target="_blank" rel="noreferrer">Report an audio or ground-truth issue ↗</a><a href="https://github.com/Hetchy/quran-alignment-benchmark/issues/new?template=new-audio.yml" target="_blank" rel="noreferrer">Suggest new audio ↗</a></div>{/if}
  <div class="filter-bar" aria-label="Recording filters">{#each facets as facet}{@const opts=options(facet.key,filters)}{#if opts.length>1||filters[facet.key]?.length}
    {#if ['style','content','noisy','multi_surah'].includes(facet.key)}<fieldset class="segmented"><legend>{facet.label}</legend><div>{#if opts.length>2}<button aria-pressed={!filters[facet.key]?.length} class:chosen={!filters[facet.key]?.length} onclick={()=>toggleFilter(facet.key,'')}>All</button>{/if}{#each opts as o}<button aria-pressed={filters[facet.key]?.includes(o)||(!filters[facet.key]?.length&&opts.length<=2)} class:chosen={filters[facet.key]?.includes(o)||(!filters[facet.key]?.length&&opts.length<=2)} onclick={()=>toggleFilter(facet.key,o)}>{label(facet.key,o)}</button>{/each}</div></fieldset>
    {:else}<label>{facet.label}<select value={filters[facet.key]?.[0]||''} onchange={e=>changeFilter(facet.key,e.currentTarget.value)}><option value="">All {facet.label.toLowerCase()}{['id','style','reciter'].includes(facet.key)?'s':''}</option>{#each opts as o}<option value={o}>{label(facet.key,o)}</option>{/each}</select></label>{/if}
  {/if}{/each}</div>
  <div class="table-toolbar"><span><strong>{selected.length}</strong> of {recordings.length} recordings <span class="dot">·</span> {totalMinutes.toFixed(0)} min {#if Object.values(filters).some(v=>v.length)}<button class="text-button" onclick={()=>{filters={};loadBoard();}}>Clear filters</button>{/if}</span>
    {#if tab==='Leaderboard'}<div class="table-actions"><input class="search" aria-label="Search systems" placeholder="Search systems" bind:value={search}/>{#if task!=='timing'}<label class="check-label"><input type="checkbox" bind:checked={extra}/> More metrics</label>{/if}</div>{/if}</div>
  {#if tab==='Leaderboard'}
    <div class="table-scroll"><table><thead><tr><th aria-sort={sortKey==='rank'?(ascending?'ascending':'descending'):'none'}><button onclick={()=>sort('rank')}>Rank {sortKey==='rank'?'↓':''}</button><ColumnHelp name="Rank" id="help-rank" text={columnHelp.rank}/></th><th class="sticky" aria-sort={sortKey==='system'?(ascending?'ascending':'descending'):'none'}><button onclick={()=>sort('system')}>System</button><ColumnHelp name="System" id="help-system" text={columnHelp.system}/></th>{#each cols as c}<th class="numeric" aria-sort={sortKey===c.key?(ascending?'ascending':'descending'):'none'}><button onclick={()=>sort(c.key)}>{c.label} {sortKey===c.key?(ascending?'↑':'↓'):''}</button><ColumnHelp name={c.label} id={`help-${c.key}`} text={columnHelp[c.key]}/></th>{/each}<th class="numeric"><button onclick={()=>sort('user_parameters')}>User-facing parameters</button><ColumnHelp name="User-facing parameters" id="help-parameters" text={columnHelp.user_parameters}/></th></tr></thead><tbody>{#each visibleRows as r}<tr><td class="rank">{r.rank}</td><td class="sticky"><button class="system-name" onclick={()=>inspect(r)}>{r.display_name||r.system} <span>↗</span></button>{#if r.synthetic}<small class="synthetic-label">Synthetic example</small>{/if}</td>{#each cols as c}<td class:numeric={true} class:headline={c.key===baseCols[0].key}>{fmt(c.key,r.scores[c.key])}{#if c.key==='rtf'&&r.scores.rtf!=null}<small>RTF {r.scores.rtf.toFixed(3)} · {r.hardware_class?.toUpperCase()}</small>{/if}</td>{/each}<td class="numeric">{r.user_parameters}</td></tr>{/each}</tbody></table></div>
    {#if boardBusy}<div class="empty" aria-live="polite">Updating results…</div>{:else if !visibleRows.length}<div class="empty"><h2>{rows.length?'No matching systems':'The first result could be yours'}</h2><p>{rows.length?'Try a different system name.':'Upload predictions, review your scores, and publish a system to this leaderboard.'}</p>{#if !rows.length}<button class="primary" onclick={()=>selectTab('Submit')}>Submit a system</button>{/if}</div>{/if}

  {:else}
    <div class="table-scroll"><table><thead><tr>{#each [{key:'id',label:'Recording'},{key:'reciter',label:'Reciter'},{key:'style',label:'Style'},{key:'content',label:'Content'},{key:'noisy',label:'Recording acoustics'},{key:'passages',label:'Passages'},{key:'duration_s',label:'Minutes'},{key:'recited_words',label:'Words'},{key:'wpm',label:'WPM'},{key:'repeat_events',label:'Repeats'}] as c}<th aria-sort={datasetSort===c.key?(datasetAsc?'ascending':'descending'):'none'}><button onclick={()=>sortDataset(c.key)}>{c.label} {datasetSort===c.key?(datasetAsc?'↑':'↓'):''}</button></th>{/each}<th>Systems</th></tr></thead><tbody>{#each datasetRows as r}<tr><td><strong>{r.id}</strong>{#if r.description}<details class="recording-description"><summary>Description</summary><p>{r.description}</p></details>{/if}</td><td>{r.reciter}</td><td>{label('style',r.style)}</td><td>{label('content',r.content)}</td><td>{label('noisy',String(r.noisy))}</td><td class="passages">{r.passages||'—'}</td><td class="numeric">{(r.duration_s/60).toFixed(1)}</td><td class="numeric">{r.recited_words.toLocaleString()}</td><td class="numeric">{r.wpm??'—'}</td><td class="numeric">{r.repeat_events}</td><td><button class="text-button" onclick={()=>rankRecording(r.id)}>Compare ↗</button></td></tr>{/each}</tbody></table></div>

  {/if}
{:else if tab==='Metrics'}{#if task==='alignment'}<Metrics version={corpus?.version}/>{:else}<TaskMetrics {task}/>{/if}
{:else}
  <div class="section-heading"><div><h1>Submit your system</h1><p>Sign in, upload predictions, review your scores, then publish.</p></div><span class="badge">{corpus?.latest} · Latest corpus</span></div>
  {#if !sessionReady}<p role="status">Checking sign-in…</p>
  {:else if !signedIn}
    <section class="sign-in-start" aria-labelledby="sign-in-title">
      <h2 id="sign-in-title">Sign in to submit</h2>
      <p>Connect your Hugging Face account before adding system details or uploading prediction files.</p>
      <button class="primary" disabled={signInBusy||!draftReady} onclick={signIn}>{signInBusy?'Opening sign-in…':'Sign in with Hugging Face ↗'}</button>
      <p class="field-help">Your account identity stays private. You will review your scores before anything is published.</p>
    </section>
  {:else}
  <p class="field-help">✓ Signed in with Hugging Face</p>
  {#if draftStatus}<p class="field-help" role="status">{draftStatus}</p>{/if}
  <form bind:this={form} onsubmit={e=>{e.preventDefault();validate();}}>
    <div class="submission-layout"><section><h2>System details</h2><div class="form-grid">
      <label>System name<input required maxlength="80" bind:value={system} oninput={invalidate} placeholder="Your system’s public name"/></label><label>Website or repository<input required type="url" bind:value={url} oninput={invalidate} placeholder="https://"/></label>
      <label class="full">Description<textarea required minlength="10" maxlength="2000" rows="4" bind:value={description} oninput={invalidate} placeholder="What does your system do, and who is it for?"></textarea></label>
      <label>User-facing parameters<input required type="number" min="0" max="10000" step="1" bind:value={userParameters} oninput={invalidate}/></label><p class="field-help">Count result-affecting controls available to intended users: demo controls or documented package options. Exclude internal, low-level developer parameters.</p>
      {#if userParameters>0}<p class="field-help full">Recommended: briefly describe these controls above and how they help with different recordings or uses.</p>{/if}
      <label class="full">Contact email <span class="private-label">Private</span><input required type="email" bind:value={email} oninput={invalidate} placeholder="For organizer contact only"/></label>
    </div><h2 class="subheading">Tasks</h2><div class="task-options">{#each Object.entries(taskNames) as [key,name]}<label><input type="checkbox" checked={selectedTasks.includes(key)} onchange={()=>toggleTask(key)}/> {name}</label>{/each}</div><p class="field-help">Select the tasks to publish together. Each keeps its own scores and replaces only its own previous result.</p>
    <h2 class="subheading">Run profile</h2><fieldset class="segmented profile-selector"><legend>Execution hardware</legend><div><button type="button" aria-pressed={hardwareClass==='cpu'} class:chosen={hardwareClass==='cpu'} onclick={()=>switchProfile('cpu')}>CPU</button><button type="button" aria-pressed={hardwareClass==='gpu'} class:chosen={hardwareClass==='gpu'} onclick={()=>switchProfile('gpu')}>GPU</button></div></fieldset><p class="field-help profile-help">Upload the predictions from this run. You can submit both CPU and GPU runs under the same system name; each appears separately and replaces only its own previous result.</p><label class="hardware-description">Hardware description <span class="private-label">Required only when reporting runtime</span><input maxlength="200" bind:value={hardware} oninput={invalidate} placeholder="e.g. 1× NVIDIA L40S, 8 vCPU"/></label>

    </section><section><fieldset class="segmented"><legend>Files for task</legend><div>{#each selectedTasks as key}<button type="button" class:chosen={task===key} aria-pressed={task===key} onclick={()=>switchTask(key)}>{taskNames[key]}</button>{/each}</div></fieldset><h2>{taskNames[task]} prediction files</h2><p class="field-help">One <code>&lt;id&gt;.json</code> per recording. Upload a ZIP or individual JSON files. Drop another file with the same ID to replace it.</p>
    <div class="dropzone" role="region" aria-label="Prediction file drop area" ondragover={e=>e.preventDefault()} ondrop={e=>{e.preventDefault();if(e.dataTransfer)addFiles(e.dataTransfer.files);}}><span class="upload-icon" aria-hidden="true">↑</span><strong>Drop prediction files here</strong><span>ZIP or JSON · up to 20 MB total</span><label class="file-button">Choose files<input aria-label="Upload prediction files" type="file" multiple accept=".json,.zip" onchange={e=>{if(e.currentTarget.files)addFiles(e.currentTarget.files);e.currentTarget.value='';}}/></label></div>
    {#if task==='alignment'}<SubmissionFormat/>{:else}<TaskFormat {task}/>{/if}
    {#each uploadIssues as issue}<p class="alert" role="alert">{issue}</p>{/each}
    <div class="upload-list" aria-live="polite">{#each corpus?.recordings||[] as r}{@const status=preview?.recordings?.find((x:Row)=>x.id===r.id)}<div class="upload-row"><div><span>{r.id}</span>{#if status?.error}<small class="error-text">{status.error}</small>{/if}</div><span class:valid={status?.status==='valid'} class="upload-status">{status?.status==='valid'?'✓ Valid':status?.status==='invalid'?'Invalid':uploaded[r.id+'.json']?'Ready to validate':'Missing'}</span>{#if uploaded[r.id+'.json']}<button type="button" class="remove" aria-label={'Remove '+r.id} onclick={()=>removeFile(r.id+'.json')}>×</button>{/if}</div>{/each}</div>
    {#if preview}{#each Object.entries(preview.file_errors||{}) as [name,message]}<p class="alert">{name}: {message} <button type="button" onclick={()=>removeFile(name)}>Remove file</button></p>{/each}{#each preview.errors as issue}<p class="alert">{issue}</p>{/each}{/if}
    <button type="submit" disabled={busy||!hasFiles()}>{busy?'Validating and scoring…':'Preview all selected tasks'}</button>
    </section></div>
  </form>
  {#if batchPreview}<section class="preview"><h2>Review all selected tasks</h2>
    <div class="batch-results">{#each selectedTasks as key}{@const result=batchPreview.tasks[key]}
      <section class="task-result"><h3>{taskNames[key]} <span class="badge">{result.complete?'Ready':'Needs attention'}</span></h3>
      <p>{result.valid} of {result.total} recordings validated.{result.replacement?' Replaces your existing '+taskNames[key]+' results.':''}</p>
      {#if result.scores}<div class="preview-scores">{#each Object.entries(result.scores).filter(([metric])=>['words_f1','clean_segments','repeats_f1','trusted_coverage','unsafe_green','segments_f1','boundaries_f1','rtf','words_timed','clean_clips'].includes(metric)) as [metric,value]}<div><span>{({words_f1:'Words F1',clean_segments:'Clean segments',repeats_f1:'Repeats F1',trusted_coverage:'Trusted coverage',unsafe_green:'Unsafe green',segments_f1:'Segments F1',boundaries_f1:'Boundaries F1',rtf:'Processing speed',words_timed:'Words timed correctly',clean_clips:'Clean clips'} as Record<string,string>)[metric]}</span><strong>{fmt(metric,value)}</strong>{#if metric==='rtf'&&value!=null}<small>RTF {Number(value).toFixed(3)} · self-reported</small>{/if}</div>{/each}</div>{/if}
      {#each result.errors as issue}<p class="alert">{issue}</p>{/each}
      {#each result.recordings.filter((r:Row)=>r.status==='invalid') as r}<p class="error-text">{r.id}: {r.error}</p>{/each}
      {#each Object.entries(result.file_errors||{}) as [name,message]}<p class="error-text">{name}: {message}</p>{/each}
      {#if result.report}<button class="text-button" onclick={()=>downloadReport(key)}>Download {taskNames[key]} report</button>{/if}
      {#if !result.complete}<button class="text-button" onclick={()=>switchTask(key)}>Manage {taskNames[key]} files</button>{/if}
      </section>
    {/each}</div>
    {#if batchPreview.owned_by_another}<p class="alert">This system name belongs to another account. Choose your own system name.</p>{:else}<button class="primary" disabled={!batchPreview.complete||busy} onclick={review}>Review & publish {selectedTasks.length===1?'task':'all '+selectedTasks.length+' tasks'}</button>{/if}
    {#if !batchPreview.complete}<p class="field-help">Resolve the listed issues, or deselect a task to publish the others.</p>{/if}
  </section>{/if}
  {/if}
  {#if notice}<p role="status" class="alert">{notice}</p>{/if}
  <p class="privacy-note">Prediction files are stored privately and retained indefinitely for reproducibility and integrity checks. Scores and the system details you provide are public. Prediction files, account identity, and contact email are not published.</p>
{/if}
</main>


<dialog bind:this={dialog} aria-labelledby="system-title"><div class="dialog-heading"><h2 id="system-title">{detail?.display_name||detail?.system}</h2><button class="close" aria-label="Close system details" onclick={()=>dialog.close()}>×</button></div>{#if detail}{#if detail.synthetic}<p class="synthetic-label">Synthetic example generated from the reference annotations for UI testing. Not a real system result.</p>{/if}<p class="system-description">{detail.description}</p><a href={detail.url} target="_blank" rel="noreferrer">Website / repository ↗</a><dl class="system-facts"><dt>Benchmark task</dt><dd>{taskNames[detail.task]||taskNames[detail.tasks?.[0]]}</dd><dt>Submission</dt><dd>{detail.corpus_version||corpus?.version} · {detail.recording_count??recordings.length} recordings<br/><span class="fact-note">Submitted {submittedAt(detail.submitted_at)}</span></dd><dt>Run profile</dt><dd>{detail.hardware_class?.toUpperCase()||'Not reported'}{#if detail.hardware}<br/><span class="fact-note">{detail.hardware}</span>{/if}</dd>{#if detail.scores?.rtf!=null}<dt>Processing speed</dt><dd>{fmtSpeed(detail.scores.rtf)}<br/><span class="fact-note">RTF {detail.scores.rtf.toFixed(3)} · self-reported</span></dd>{/if}<dt>User-facing parameters</dt><dd>{detail.user_parameters}</dd></dl>{/if}</dialog>
<dialog bind:this={confirmDialog} aria-labelledby="publish-title"><div class="dialog-heading"><h2 id="publish-title">Publish {selectedTasks.length===1?'this task':'all '+selectedTasks.length+' tasks'}?</h2><button class="close" aria-label="Close publication review" disabled={publishBusy} onclick={()=>confirmDialog.close()}>×</button></div>
<p><strong>{system} ({hardwareClass.toUpperCase()})</strong> · {corpus?.latest}</p>
{#each selectedTasks as key}{@const result=batchPreview?.tasks[key]}<p><strong>{taskNames[key]}</strong>: {fmt(key==='alignment'?'words_f1':key==='segmentation'?'segments_f1':'words_timed',result?.scores?.[key==='alignment'?'words_f1':key==='segmentation'?'segments_f1':'words_timed'])} · {result?.valid} recordings · {result?.replacement?'Replace existing result':'New result'}</p>{/each}
<p>All listed tasks will publish together. Your description, link, parameter count, hardware details, and scores will be public. Files and contact information stay private.</p>
<label class="consent"><input type="checkbox" bind:checked={consent}/> I confirm these runs did not use benchmark annotations, training or tuning on the benchmark, or manual prediction corrections.{selectedTasks.includes('timing')?' Word timing may use the supplied clips and references, but not reference word timestamps.':''} Public user-facing controls are allowed.</label>
<div class="dialog-actions"><button disabled={publishBusy} onclick={()=>confirmDialog.close()}>Back to preview</button><button class="primary" disabled={!consent||publishBusy} onclick={publish}>{publishBusy?'Publishing…':selectedTasks.length===1?'Publish task':'Publish all '+selectedTasks.length+' tasks'}</button></div>{#if notice}<p role="alert" class="alert">{notice}</p>{/if}</dialog>
