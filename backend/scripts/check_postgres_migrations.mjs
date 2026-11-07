// Optional PostgreSQL WASM check: set PYTHON and PGLITE_MODULE_ROOT; see README.
process.on('uncaughtException', e => { console.error(JSON.stringify({message:e.message,code:e.code,hint:e.hint,position:e.position,where:e.where})); process.exit(1); });
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
const backend = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(resolve(process.env.PGLITE_MODULE_ROOT || backend, 'package.json'));
const { PGlite } = require('@electric-sql/pglite');
function sql(target) {
  const result = spawnSync(process.env.PYTHON || 'python', ['-m', 'alembic', 'upgrade', target, '--sql'], {
    cwd: backend, encoding: 'utf8', timeout: 60000,
    env: {...process.env, DATABASE_URL: 'postgresql+asyncpg://unused:unused@localhost/test'},
  });
  if (result.status !== 0) throw new Error(result.stderr || 'Failed to export migration SQL');
  return result.stdout;
}
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
const db = new PGlite();
await db.exec(sql('20261101_01'));
const ids = Object.fromEntries(['user','draft','task','partA','partB','stepA','stepB','candidate','preference'].map(k=>[k,randomUUID()]));
const record = {created_at:'2026-11-01T00:00:00Z', updated_at:'2026-11-01T00:00:00Z', revision:1};
async function insert(table, values, connection=db) {
 const data={...record,...values}; const cols=Object.keys(data);
 await connection.query(`INSERT INTO ${table} (${cols.join(',')}) VALUES (${cols.map((_,i)=>'$'+(i+1)).join(',')})`,Object.values(data).map(v=>v && typeof v==='object'?JSON.stringify(v):v));
}
await insert('users',{id:ids.user,email:'pgcheck@example.test',display_name:'Migration test',is_active:true,current_streak_days:0,streak_frozen:false,streak_freeze_tokens:2});
await insert('user_preferences',{id:ids.preference,user_id:ids.user,work_interval_seconds:1500,rest_interval_seconds:300,micro_task_seconds:null,reminder_offsets:[300],effective_revision:1,buffer_multiplier:1.2,reminder_style:'gentle'});
await insert('planning_drafts',{id:ids.draft,user_id:ids.user,client_draft_id:'legacy',stage:'candidate_review',input_source:'text',summary:{},summary_revision:1,summary_confirmed_revision:1,planning_input_revision:1});
await insert('tasks',{id:ids.task,user_id:ids.user,title:'Legacy task',time_constraint:{kind:'deadline'},status:'pending',avoidance_count:0,source_draft_id:ids.draft,source_summary:{availability:{timezone:'UTC',weekly_rules:[],date_overrides:[]}}});
for (const [key,order,state] of [['partA',0,'complete'],['partB',1,'partial']]) {
 await insert('planning_parts',{id:ids[key],user_id:ids.user,task_id:ids.task,client_part_key:key,scope_description:key,order_index:order,decomposition_state:state,scheduling_state:'not_scheduled',unexpanded_scope:state==='partial'?'Unexpanded remainder':null});
}
for (const [key,part] of [['stepB','partB'],['stepA','partA']]) {
 await insert('steps',{id:ids[key],user_id:ids.user,task_id:ids.task,planning_part_id:ids[part],title:key,expected_output:key,order_index:0,estimated_base_seconds:1500,remaining_base_seconds:1500,buffer_multiplier:1.2,estimate_source:'ai_suggested',status:'pending'});
}
const parts=[{client_part_key:'partB',order_index:1,decomposition_state:'partial',scope_description:'partB',unexpanded_scope:'Unexpanded remainder'},{client_part_key:'partA',order_index:0,decomposition_state:'complete',scope_description:'partA'}];
const steps=[{client_step_key:'stepB',client_part_key:'partB',order_index:0,_micro_task_text:'Preserve micro B'},{client_step_key:'stepA',client_part_key:'partA',order_index:0,_micro_task_text:'Preserve micro A'}];
await insert('plan_candidates',{id:ids.candidate,user_id:ids.user,draft_id:ids.draft,source_summary_revision:1,source_planning_input_revision:1,review_revision:1,planning_parts:parts,steps,sessions:[],planning_horizon:{},source_revisions:{},feasibility:{verification_scope:{scope_type:'candidate_parts',client_part_keys:['partA'],planning_horizon:{}},unplanned_part_ids:['partB'],unestimated_part_ids:[]},min_inter_session_gap_seconds:900});
await db.exec(sql('20261101_01:head'));
const candidate=(await db.query('SELECT * FROM plan_candidates')).rows[0];
assert.equal(candidate.schema_version,1);
assert.equal(candidate.decomposition_state,'partial');
assert.equal(candidate.unexpanded_scope,'Unexpanded remainder');
assert.deepEqual(candidate.steps.map(s=>[s.client_step_key,s.order_index]),[['stepA',0],['stepB',1]]);
assert.equal(candidate.steps[0]._micro_task_text,'Preserve micro A');
assert.ok(candidate.steps.every(s=>!('client_part_key' in s)));
assert.deepEqual(candidate.feasibility.verification_scope.client_step_keys,['stepA']);
assert.equal(candidate.feasibility.verification_scope.scope_type,'candidate_steps');
const task=(await db.query('SELECT * FROM tasks')).rows[0];
assert.equal(task.decomposition_state,'partial');
assert.equal(task.unexpanded_scope,'Unexpanded remainder');
assert.equal(task.availability.timezone,'UTC');
const savedSteps=(await db.query('SELECT title,order_index FROM steps ORDER BY order_index')).rows;
assert.deepEqual(savedSteps,[{title:'stepA',order_index:0},{title:'stepB',order_index:1}]);
const pref=(await db.query('SELECT micro_task_seconds,reminder_offsets FROM user_preferences')).rows[0];
assert.equal(pref.micro_task_seconds,120); assert.deepEqual(pref.reminder_offsets,[300]);
assert.equal((await db.query('SELECT version_num FROM alembic_version')).rows[0].version_num,'20261107_01');
await db.close();
const fresh=new PGlite();
await fresh.exec(sql('head'));
assert.equal((await fresh.query('SELECT version_num FROM alembic_version')).rows[0].version_num,'20261107_01');
await fresh.close();
const legacy=new PGlite();
await legacy.exec(sql('20261030_01'));
await insert('users',{id:ids.user,email:'legacy@example.test',display_name:'Legacy',is_active:true,current_streak_days:0,streak_frozen:false,streak_freeze_tokens:2},legacy);
await insert('tasks',{id:ids.task,user_id:ids.user,title:'Legacy cast',time_constraint:{},importance:'3',status:'pending',avoidance_count:0},legacy);
await insert('audit_events',{id:randomUUID(),user_id:ids.user,event_id:'legacy-event',event_type:'legacy',resource_ref:'task:original-id',occurred_at:record.created_at,received_at:record.created_at},legacy);
await legacy.exec(sql('20261030_01:20261101_01'));
assert.equal((await legacy.query('SELECT importance FROM tasks')).rows[0].importance,3);
assert.equal((await legacy.query('SELECT resource_ref FROM audit_events')).rows[0].resource_ref,'task:original-id');
await legacy.close();
console.log('PASS: PostgreSQL WASM fresh schema and populated C01/device upgrade, provenance/order/micro-task/selected scope preserved');
