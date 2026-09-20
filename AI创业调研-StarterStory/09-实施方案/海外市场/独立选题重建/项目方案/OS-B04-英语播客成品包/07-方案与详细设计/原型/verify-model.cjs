'use strict';
const assert = require('node:assert/strict');
const {fresh,transition,validate,srt} = require('./logic.js');
const clone = x => JSON.parse(JSON.stringify(x));
const backup = e => ({schema:1,product:'OS-B04',episodes:[clone(e)]});
let passed=0,failed=0;
function test(name,fn){try{fn();passed++;console.log('PASS '+name);}catch(err){failed++;console.error('FAIL '+name+' — '+err.message);}}
function edit(){return transition(fresh(),'brief',{duration:1800,rights:true},'client');}
function ready(){let e=edit();for(const c of e.clips)e=transition(e,'saveClip',{clipId:c.id,start:c.start,end:c.end,checked:true},'editor');return e;}
function review(){return transition(ready(),'send',{},'editor');}
function approved(){let e=review();for(const c of e.clips)e=transition(e,'approve',{clipId:c.id,version:e.version,read:true},'client');return e;}
function manifest(){return transition(approved(),'manifest',{},'editor');}
function rejectsMutation(name,seed,mutate){test('backup rejects '+name,()=>{const db=backup(seed());mutate(db.episodes[0],db);assert.throws(()=>validate(db));});}

test('fresh and every reachable main phase validate',()=>{for(const e of [fresh(),edit(),ready(),review(),approved(),manifest()])assert.equal(validate(backup(e)).episodes[0].phase,e.phase);});
test('transition preserves its input',()=>{const e=ready(),original=clone(e);transition(e,'send',{},'editor');assert.deepEqual(e,original);});
test('source rights and duration required',()=>{assert.throws(()=>transition(fresh(),'brief',{duration:1800,rights:false},'client'));for(const duration of [89,3601,90.5,NaN])assert.throws(()=>transition(fresh(),'brief',{duration,rights:true},'client'));});
test('role guards cover all actions',()=>{for(const [action,e,p,role] of [ ['brief',fresh(),{duration:1800,rights:true},'editor'],['saveClip',edit(),{clipId:'clip-1',start:120,end:165},'client'],['saveChapters',edit(),{chapters:'Intro'},'client'],['send',ready(),{},'client'],['approve',review(),{clipId:'clip-1',version:1,read:true},'editor'],['changes',review(),{clipId:'clip-1',version:1,feedback:'Fix context'},'editor'],['revision',review(),{},'client'],['manifest',approved(),{},'client']])assert.throws(()=>transition(e,action,p,role),action);});
test('clip range accepts 30 and 90 seconds and source endpoints',()=>{for(const [start,end] of [[0,30],[0,90],[1770,1800]])assert.equal(transition(edit(),'saveClip',{clipId:'clip-1',start,end},'editor').clips[0].end,end);});
test('clip range rejects short/long/reversed/outside/fractional/nonfinite',()=>{for(const [start,end] of [[0,29],[0,91],[90,0],[-1,44],[1771,1801],[1.5,46.5],[NaN,45],[0,Infinity]])assert.throws(()=>transition(edit(),'saveClip',{clipId:'clip-1',start,end},'editor'));});
test('cannot send unchecked excerpts',()=>assert.throws(()=>transition(edit(),'send',{},'editor')));
test('review freezes clips chapters and brief',()=>{const e=review();for(const [action,p,role] of [['saveClip',{clipId:'clip-1',start:120,end:165},'editor'],['saveChapters',{chapters:'Changed'},'editor'],['brief',{duration:1800,rights:true},'client']])assert.throws(()=>transition(e,action,p,role));});
test('approval requires reading and current version',()=>{for(const p of [{clipId:'clip-1',version:0,read:true},{clipId:'clip-1',version:1,read:false}])assert.throws(()=>transition(review(),'approve',p,'client'));});
test('feedback requires text and current version',()=>{for(const p of [{clipId:'clip-1',version:0,feedback:'Fix'},{clipId:'clip-1',version:1,feedback:' '}])assert.throws(()=>transition(review(),'changes',p,'client'));});
test('requested changes block manifest and require revision before approval',()=>{let e=transition(review(),'changes',{clipId:'clip-1',version:1,feedback:'Preserve qualifier'},'client');assert.equal(e.clips[0].status,'changes');assert.equal(e.history[0].decisions[0].feedback,'Preserve qualifier');assert.throws(()=>transition(e,'manifest',{},'editor'));assert.throws(()=>transition(e,'approve',{clipId:'clip-1',version:1,read:true},'client'));});
test('partial approval is not all approved',()=>{const e=transition(review(),'approve',{clipId:'clip-1',version:1,read:true},'client');assert.equal(e.phase,'review');assert.throws(()=>transition(e,'manifest',{},'editor'));});
test('new revision clears approvals checked flags and manifest',()=>{const e=transition(manifest(),'revision',{},'editor');assert.equal(e.version,2);assert.equal(e.phase,'editing');assert.equal(e.manifest,null);assert.ok(e.clips.every(c=>c.status==='draft'&&!c.checked));assert.ok(e.history[0].decisions.every(c=>c.status==='approved'));});
test('revision history preserves original content and denies stale approval',()=>{let e=transition(approved(),'revision',{},'editor');const old=clone(e.history[0]);for(const c of e.clips)e=transition(e,'saveClip',{clipId:c.id,start:c.start,end:c.end,title:'v2 '+c.title,checked:true},'editor');e=transition(e,'send',{},'editor');assert.equal(e.history.length,2);assert.deepEqual(e.history[0],old);assert.notEqual(e.history[0].clips[0].title,e.history[1].clips[0].title);assert.throws(()=>transition(e,'approve',{clipId:'clip-1',version:1,read:true},'client'));assert.equal(validate(backup(e)).episodes[0].version,2);});
test('manifest declares missing production assets and unrendered status',()=>{const m=manifest().manifest;assert.equal(m.type,'prototype-review-manifest');assert.equal(m.status,'review-approved-media-not-rendered');assert.ok(m.missingProductionAssets.includes('3 rendered MP4 clips'));assert.ok(m.missingProductionAssets.includes('source-checked timed captions'));});
test('SRT is one review cue relative to clip not source time',()=>assert.equal(srt({start:120,end:165,quote:'Exact excerpt'}),'1\n00:00:00,000 --> 00:00:45,000\nExact excerpt\n'));
test('backup roundtrip preserves all model state',()=>{const db=backup(manifest());assert.deepEqual(validate(JSON.parse(JSON.stringify(db))),db);});
rejectsMutation('wrong product',fresh,(e,db)=>db.product='other');
rejectsMutation('duplicate episode id',fresh,(e,db)=>db.episodes.push(clone(e)));
rejectsMutation('duplicate clip id',fresh,e=>e.clips[1].id=e.clips[0].id);
rejectsMutation('invalid clip interval after submission',review,e=>e.clips[0].end=e.clips[0].start+29);
rejectsMutation('missing current snapshot',review,e=>e.history=[]);
rejectsMutation('missing source rights',review,e=>e.rights=false);
rejectsMutation('unchecked approved source',approved,e=>e.clips[0].checked=false);
rejectsMutation('unapproved clip in approved phase',approved,e=>e.clips[0].status='pending');
rejectsMutation('missing manifest',manifest,e=>e.manifest=null);
rejectsMutation('manifest wrong version',manifest,e=>e.manifest.version=0);
rejectsMutation('empty episode id',fresh,e=>e.id='');
rejectsMutation('editing without rights',edit,e=>e.rights=false);
rejectsMutation('draft clip carrying approved status',fresh,e=>e.clips[0].status='approved');
rejectsMutation('review clip carrying draft status',review,e=>e.clips[0].status='draft');
rejectsMutation('snapshot clip ids mismatch',review,e=>e.history[0].clips[0].id='other');
rejectsMutation('snapshot invalid timing',review,e=>e.history[0].clips[0].end=-1);
rejectsMutation('snapshot content differs from frozen current clip',review,e=>e.clips[0].quote='Replaced after client was shown snapshot');
rejectsMutation('snapshot decisions contradict approved clips',approved,e=>e.history[0].decisions[0].status='pending');
rejectsMutation('unknown decision status',review,e=>e.history[0].decisions[0].status='forged');
rejectsMutation('manifest contains null clips',manifest,e=>e.manifest.clips=[null,null,null]);
rejectsMutation('manifest clips differ from approved source',manifest,e=>e.manifest.clips[0].quote='Unapproved replacement');
rejectsMutation('manifest missing production disclaimer',manifest,e=>delete e.manifest.missingProductionAssets);
rejectsMutation('future event version',review,e=>e.events[0].version=999);
console.log(`\n${passed} passed, ${failed} failed`);process.exitCode=failed?1:0;
