# STATE Schema

`STATE.md` YAML-benzeri, checkpoint kaynağıdır.

Zorunlu alanlar: `schema_version`, `program`, `mode`, `status`, `current_wave`, `current_step`, `last_completed_wave`, `next_wave`, `retry_count`, `run_id`, `lock_status`, `branch`, `head`, `worktree_state`, canonical sayaçlar, `final_decision`, `installation_complete`, `queue_initialized`, `runner_started`, `updated_at`.

İzinli `status` değerleri: `INSTALLED`, `READY`, `RUNNING`, `PAUSED`, `BLOCKED`, `DECISION_REQUIRED`, `COMPLETED`, `FAILED_SAFE`.

İzinli `current_step` değerleri: `IDLE`, `PLAN`, `REPRODUCE`, `FAILURE_CONFIRMED`, `PATCH`, `TARGET_TEST`, `REGRESSION_TEST`, `CROSS_SYSTEM_CHECK`, `CONTROLLED_RECOVERY`, `RUNTIME_VALIDATE`, `RECONCILE`, `CHECKPOINT`, `DECISION_REQUIRED`.

`current_wave` yalnız aktif wave veya `null`; `runner_started` boolean; canonical sayaçlar tamsayı olmalıdır. Her güncelleme atomik persist edilir.
