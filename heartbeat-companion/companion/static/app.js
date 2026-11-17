"use strict";

const $ = (id) => document.getElementById(id);
const state = {data:null, tab:"chat", loading:false, sending:false, settingsDirty:false, personaDirty:false, photoURLs:new Map(), photoGeneration:0, token:sessionStorage.getItem("heartbeat_token") || "", selectedArtifact:null, lastMessageSignature:""};
const tabNames = {chat:"聊天",activity:"心跳记录",album:"相册",memory:"记忆与计划",settings:"设置"};
const activityNames = {chat:"接上聊天",album_search:"寻找照片",memory_read:"找回一段记忆",memory_write:"记下一件事",plan:"更新手头计划",artifact:"留下一个成果",artifact_read:"翻开保存的文档",finished:"本次活动结束",silent:"这次保持安静",settings:"调整设置",upload:"加入一张照片",recovery:"恢复运行记录",wake:"醒来看看",wake_started:"一次新的心跳",wake_finished:"这次先到这里",wake_completed:"这次先到这里",run_started:"开始运行",run_completed:"本次运行完成",tool:"使用工具",tool_call:"使用工具",photo_read:"翻了一张照片",photo_search:"寻找照片",memory_search:"找回一段记忆",memory_updated:"记下一件事",plan_updated:"更新手头计划",artifact_created:"留下一个成果",scheduled:"预约下一次",schedule:"预约下一次",message:"发来一条消息",message_sent:"发来一条消息",error:"这次遇到一点问题",cancelled:"接上新的聊天",deferred:"留到合适的时候",budget:"今天先歇一歇",demo:"演示流程"};

function element(tag, className, text) { const el = document.createElement(tag); if(className) el.className = className; if(text !== undefined) el.textContent = text; return el; }
function formatTime(value, full=false) { if(!value) return "—"; const date = new Date(value); if(Number.isNaN(date.getTime())) return String(value); const options = {timeZone:state.data?.timezone || "Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false}; if(full) Object.assign(options,{month:"2-digit",day:"2-digit"}); return new Intl.DateTimeFormat("zh-CN", options).format(date); }
function readable(value) { if(value === undefined || value === null) return ""; return typeof value === "string" ? value : JSON.stringify(value,null,2); }
function empty(container, text, className="empty-state") {container.replaceChildren(element("div",className,text));}
function showError(error) {$("error-banner").textContent = error instanceof Error ? error.message : String(error); $("error-banner").hidden = false;}
let successTimeout;
function showSuccess(text) {$("success-banner").textContent=text; $("success-banner").hidden=false; clearTimeout(successTimeout); successTimeout=setTimeout(()=>{$("success-banner").hidden=true;},4000);}
async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if(state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if(options.body && !(options.body instanceof FormData)) headers.set("Content-Type","application/json");
  let response;
  try {response=await fetch(path,{...options,headers,cache:"no-store"});} catch {throw new Error("暂时连接不上程序。请确认服务仍在运行。");}
  if(!response.ok) {
    if(response.status===401 || response.status===403) throw new Error("访问口令未通过。请在「设置」里填写服务端配置的访问令牌。");
    let message=`操作没有完成（${response.status}）`;
    try {const body=await response.json(); if(body.detail) message=typeof body.detail==="string"?body.detail:JSON.stringify(body.detail); else if(body.error) message=String(body.error);} catch {}
    throw new Error(message);
  }
  if(options.responseType==="blob") return response.blob();
  if(response.status===204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
const post = (path,data) => api(path,{method:"POST",body:JSON.stringify(data)});

function setTab(tab) {
  if(!tabNames[tab]) tab="chat";
  state.tab=tab;
  document.querySelectorAll(".tab-panel").forEach(panel=>panel.hidden=panel.id!==`panel-${tab}`);
  document.querySelectorAll(".nav-button").forEach(button=>{const active=button.dataset.tab===tab;button.classList.toggle("active",active);if(active) button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");});
  $("page-title").textContent=tabNames[tab];
  if(location.hash!==`#${tab}`) history.replaceState(null,"",`#${tab}`);
}

function renderMessages(messages) {
  const signature=JSON.stringify(messages);
  if(signature===state.lastMessageSignature) return;
  state.lastMessageSignature=signature;
  const container=$("messages");
  const nearBottom=container.scrollHeight-container.scrollTop-container.clientHeight<130;
  if(!messages.length) {
    const emptyBox=element("div","empty-chat");
    emptyBox.append(element("div","empty-symbol","✧"),element("h2","","从一句话开始。"),element("p","","聊聊今天，或留下一件想继续的事。小机会带着这些上下文，决定下一次醒来时做些什么。"));
    emptyBox.append(element("div","hint",state.data?.mode==="demo"?"演示可以这样试：先聊一句，再点「现在唤醒一次」。":"先聊一句，再把想让它翻到的照片放进相册。"));
    container.replaceChildren(emptyBox);return;
  }
  const fragment=document.createDocumentFragment();
  messages.forEach(message=>{
    const row=element("article",`message ${message.role==="user"?"user":"assistant"}`);
    const meta=element("div","message-meta");
    meta.append(element("span","",message.role==="user"?"你":"小机"),element("time","",formatTime(message.created_at,true)));
    if(message.kind && !["chat","user","reply"].includes(message.kind)) meta.append(element("span","message-kind",message.kind==="proactive"?"主动消息":message.kind==="demo"?"演示":message.kind));
    row.append(meta,element("div","message-bubble",message.content));fragment.append(row);
  });
  container.replaceChildren(fragment);
  if(nearBottom || state.sending) container.scrollTop=container.scrollHeight;
}

function renderActivities(activities) {
  const container=$("activities");
  if(!activities.length) return empty(container,"还没有后台活动。一次唤醒之后，实际做过的事会留在这里。");
  const fragment=document.createDocumentFragment();
  [...activities].reverse().forEach(item=>{
    const row=element("article","activity-item");
    row.append(element("time","activity-time",formatTime(item.created_at,true).replace(" ","\n")),element("div","activity-marker"));
    const body=element("div");body.append(element("h3","activity-title",activityNames[item.kind] || item.kind),element("p","activity-detail",readable(item.detail)));
    if(item.run_id) body.append(element("div","activity-run",`运行 ${String(item.run_id).slice(0,12)}`));
    row.append(body);fragment.append(row);
  });container.replaceChildren(fragment);
}

function renderArtifacts(artifacts) {
  $("artifact-count").textContent=artifacts.length;
  const container=$("artifacts");
  if(!artifacts.length) return empty(container,"选好的照片、写下的文字……\n完成后，会留在这里。");
  const fragment=document.createDocumentFragment();
  [...artifacts].reverse().forEach(artifact=>{const button=element("button","artifact-button");button.type="button";button.append(element("span","",artifact.title || "未命名成果"),element("span","","↗"));button.addEventListener("click",()=>openArtifact(artifact));fragment.append(button);});container.replaceChildren(fragment);
}
function openArtifact(artifact) {state.selectedArtifact=artifact;$("artifact-title").textContent=artifact.title || "留下来的东西";$("artifact-content").textContent=artifact.content || "";$("artifact-dialog").showModal();}

async function loadPhoto(photo, wrapper) {
  const generation=state.photoGeneration;
  try {
    let url=state.photoURLs.get(String(photo.id));
    if(!url) {const blob=await api(`/api/photos/${encodeURIComponent(photo.id)}/image`,{responseType:"blob"});if(generation!==state.photoGeneration || !wrapper.isConnected)return;url=URL.createObjectURL(blob);if(state.photoURLs.has(String(photo.id))) URL.revokeObjectURL(url);else state.photoURLs.set(String(photo.id),url);url=state.photoURLs.get(String(photo.id));}
    if(!wrapper.isConnected) return;
    const img=element("img");img.src=url;img.alt=photo.title || "相册照片";img.loading="lazy";wrapper.replaceChildren(img);
  } catch(error) {if(wrapper.isConnected) {wrapper.textContent="照片暂时没有加载成功";wrapper.title=error.message;}}
}
let photoSignature="";
function renderPhotos(photos) {
  const signature=JSON.stringify(photos);
  if(signature===photoSignature) return;
  photoSignature=signature;
  const ids=new Set(photos.map(photo=>String(photo.id)));
  for(const [id,url] of state.photoURLs) if(!ids.has(id)){URL.revokeObjectURL(url);state.photoURLs.delete(id);}
  const container=$("photos");
  if(!photos.length) return empty(container,"相册还是空的。\n放一张照片，再给它留一点关于这张照片的记忆。");
  const fragment=document.createDocumentFragment();const loaders=[];
  [...photos].reverse().forEach(photo=>{
    const card=element("article","card photo-card");const imageWrapper=element("div","photo-image-wrap","正在翻开照片……");
    const body=element("div","photo-body");body.append(element("h3","",photo.title || "未命名照片"),element("p","",photo.caption || "还没有配文"));
    const bottom=element("div","item-bottom");const remove=element("button","text-button","删除");remove.type="button";remove.addEventListener("click",()=>deleteItem(`/api/photos/${encodeURIComponent(photo.id)}`,"照片已删除。",remove));bottom.append(element("time","",formatTime(photo.created_at,true)),remove);body.append(bottom);card.append(imageWrapper,body);fragment.append(card);loaders.push(()=>loadPhoto(photo,imageWrapper));
  });container.replaceChildren(fragment);loaders.forEach(load=>load());
}

function renderNotes(memories,plans) {
  $("memory-count").textContent=memories.length;$("plan-count").textContent=plans.length;
  const memoryBox=$("memories"),planBox=$("plans");
  const sourceNames={user:"你补充的",agent:"小机记下的",assistant:"小机记下的",demo:"演示记录"};
  if(!memories.length) empty(memoryBox,"还没有记忆。先留下一件想被记住的事。");
  else {const fragment=document.createDocumentFragment();[...memories].reverse().forEach(memory=>{
    const card=element("article","card note-card");card.append(element("h3","",memory.key),element("p","",memory.content));
    const bottom=element("div","item-bottom");bottom.append(element("span","",`${sourceNames[memory.source] || memory.source || "记忆"} · ${formatTime(memory.updated_at,true)}`));const button=element("button","text-button","删除");button.type="button";button.addEventListener("click",()=>deleteItem(`/api/memories/${encodeURIComponent(memory.id)}`,"记忆已删除。",button));bottom.append(button);card.append(bottom);fragment.append(card);
  });memoryBox.replaceChildren(fragment);}
  const statusNames={open:"待继续",active:"进行中",in_progress:"进行中",done:"已完成",completed:"已完成",cancelled:"已放下",abandoned:"已放下"};
  if(!plans.length) empty(planBox,"手头还没有计划。有了进度，就从这里接着做。");
  else {const fragment=document.createDocumentFragment();[...plans].reverse().forEach(plan=>{
    const card=element("article","card note-card");const title=element("h3","",plan.title);title.append(element("span",`plan-status ${["done","completed"].includes(plan.status)?"done":""}`,statusNames[plan.status] || plan.status));card.append(title,element("p","",plan.detail || "还没有补充进度。"));
    const bottom=element("div","item-bottom");bottom.append(element("time","",formatTime(plan.updated_at,true)));const button=element("button","text-button","删除");button.type="button";button.addEventListener("click",()=>deleteItem(`/api/plans/${encodeURIComponent(plan.id)}`,"计划已删除。",button));bottom.append(button);card.append(bottom);fragment.append(card);
  });planBox.replaceChildren(fragment);}
}

function render() {
  const data=state.data;if(!data) return;
  $("mode-badge").textContent=data.mode==="demo"?"演示模式 · 不调用模型":"真实模型已配置";$("mode-badge").classList.toggle("live",data.mode!=="demo");
  $("chat-status").textContent=data.busy?"正在处理这一刻……":"可以接着聊";
  $("connection-status").textContent=data.busy?"正在运行":data.enabled?"心跳已开启":"自动心跳已暂停";
  $("wake-heading").textContent=data.busy?"有一点事情在忙":data.next_wake?"留好了下一次时间":"等一个新的念头";
  $("wake-purpose").textContent=data.next_wake?.purpose || (data.enabled?"还没有预约。聊聊近况，或手动唤醒一次。":"自动心跳已暂停，聊天仍然可用。");
  $("wake-time").textContent=data.next_wake?formatTime(data.next_wake.due_at,true):"尚未预约";
  $("wake-button").disabled=Boolean(data.busy);$("usage-pill").textContent=`今日模型调用 ${data.usage_today ?? 0} / ${data.settings?.daily_budget ?? "—"}`;
  $("timezone-label").textContent=data.timezone || "Asia/Shanghai";
  $("provider-label").textContent=data.provider || (data.mode==="demo"?"演示":"未配置");$("model-label").textContent=data.model || "尚未配置";
  $("demo-explanation").textContent=data.mode==="demo"?"当前演示模式使用固定流程验证界面和调度，不代表模型自主决策。":"当前已启用真实模型。API 调用将由你配置的服务计费。";
  if(!state.personaDirty) $("persona-content").value=data.persona || "";
  if(!state.settingsDirty){$("enabled").checked=Boolean(data.settings?.enabled ?? data.enabled);$("quiet-start").value=data.settings?.quiet_start ?? "23:00";$("quiet-end").value=data.settings?.quiet_end ?? "08:00";$("daily-budget").value=data.settings?.daily_budget ?? 24;}
  renderMessages(data.messages || []);renderActivities(data.activities || []);renderArtifacts(data.artifacts || []);renderPhotos(data.photos || []);renderNotes(data.memories || [],data.plans || []);
  $("date-label").textContent=new Intl.DateTimeFormat("zh-CN",{timeZone:data.timezone || "Asia/Shanghai",month:"long",day:"numeric",weekday:"long"}).format(new Date()).toUpperCase();
}
async function refresh() {
  if(state.loading) return;
  state.loading=true;
  try {state.data=await api("/api/state");$("connection-dot").classList.add("online");$("error-banner").hidden=true;render();$("last-updated").textContent=`更新于 ${formatTime(new Date().toISOString())}`;}
  catch(error){$("connection-dot").classList.remove("online");$("connection-status").textContent="连接暂时中断";showError(error);}
  finally{state.loading=false;}
}
async function deleteItem(path,message,button) {
  button.disabled=true;
  try {await api(path,{method:"DELETE"});await refresh();showSuccess(message);} catch(error){showError(error);button.disabled=false;}
}
async function submitForm(form,handler) {
  const button=form.querySelector("button[type=submit]");button.disabled=true;
  try{await handler();await refresh();}catch(error){showError(error);}finally{button.disabled=false;}
}

document.querySelectorAll(".nav-button").forEach(button=>button.addEventListener("click",()=>setTab(button.dataset.tab)));
window.addEventListener("hashchange",()=>setTab(location.hash.slice(1)));
$("chat-form").addEventListener("submit",async event=>{
  event.preventDefault();if(state.sending) return;const content=$("chat-input").value.trim();if(!content) return;
  state.sending=true;$("send-button").disabled=true;
  try{await post("/api/chat",{content});if($("chat-input").value.trim()===content) $("chat-input").value="";await refresh();$("messages").scrollTop=$("messages").scrollHeight;}catch(error){showError(error);}finally{state.sending=false;$("send-button").disabled=false;$("chat-input").focus();}
});
$("chat-input").addEventListener("keydown",event=>{if(event.key==="Enter" && !event.shiftKey && !event.isComposing){event.preventDefault();$("chat-form").requestSubmit();}});
$("wake-button").addEventListener("click",async()=>{const button=$("wake-button");button.disabled=true;try{await post("/api/wake",{purpose:"手动查看"});await refresh();showSuccess("已请求唤醒，可以在心跳记录中查看实际活动。");}catch(error){showError(error);}finally{button.disabled=Boolean(state.data?.busy);}});
$("show-photo-form").addEventListener("click",()=>{$("photo-form").hidden=!$("photo-form").hidden;if(!$("photo-form").hidden) $("photo-title").focus();});
$("photo-form").addEventListener("submit",event=>{event.preventDefault();submitForm(event.currentTarget,async()=>{const data=new FormData($("photo-form"));await api("/api/photos",{method:"POST",body:data});$("photo-form").reset();$("photo-form").hidden=true;showSuccess("照片已放进相册。");});});
$("memory-form").addEventListener("submit",event=>{event.preventDefault();submitForm(event.currentTarget,async()=>{await post("/api/memories",{key:$("memory-key").value.trim(),content:$("memory-content").value.trim(),source:"user"});$("memory-form").reset();showSuccess("这件事记下来了。");});});
$("plan-form").addEventListener("submit",event=>{event.preventDefault();submitForm(event.currentTarget,async()=>{await post("/api/plans",{title:$("plan-title").value.trim(),detail:$("plan-detail").value.trim(),status:"open"});$("plan-form").reset();showSuccess("计划已留下。");});});
$("settings-form").addEventListener("input",()=>{state.settingsDirty=true;});
$("settings-form").addEventListener("submit",event=>{event.preventDefault();submitForm(event.currentTarget,async()=>{await post("/api/settings",{enabled:$("enabled").checked,quiet_start:$("quiet-start").value,quiet_end:$("quiet-end").value,daily_budget:Number($("daily-budget").value)});state.settingsDirty=false;showSuccess("心跳节奏已更新。");});});
$("persona-form").addEventListener("input",()=>{state.personaDirty=true;});
$("persona-form").addEventListener("submit",event=>{event.preventDefault();submitForm(event.currentTarget,async()=>{await post("/api/persona",{content:$("persona-content").value.trim()});state.personaDirty=false;showSuccess("称呼和交流风格已更新。");});});
function clearPhotoCache(){state.photoGeneration++;for(const url of state.photoURLs.values()) URL.revokeObjectURL(url);state.photoURLs.clear();photoSignature="";}
$("token-form").addEventListener("submit",event=>{event.preventDefault();state.token=$("access-token").value.trim();if(state.token)sessionStorage.setItem("heartbeat_token",state.token);else sessionStorage.removeItem("heartbeat_token");clearPhotoCache();refresh();});
$("clear-token").addEventListener("click",()=>{state.token="";$("access-token").value="";sessionStorage.removeItem("heartbeat_token");clearPhotoCache();refresh();});
$("access-token").value=state.token;
$("close-artifact").addEventListener("click",()=>$("artifact-dialog").close());
$("artifact-dialog").addEventListener("click",event=>{if(event.target===$("artifact-dialog")){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom) event.target.close();}});
$("download-artifact").addEventListener("click",()=>{const artifact=state.selectedArtifact;if(!artifact)return;const url=URL.createObjectURL(new Blob([artifact.content || ""],{type:"text/plain;charset=utf-8"}));const link=element("a");link.href=url;link.download=`${(artifact.title || "心跳成果").replace(/[\\/:*?"<>|]/g,"_")}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
window.addEventListener("beforeunload",clearPhotoCache);
setTab(location.hash.slice(1) || "chat");refresh();setInterval(()=>{if(!document.hidden)refresh();},3000);
