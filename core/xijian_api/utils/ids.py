"""ID generators used across the XiJian API server.
隙间 API 服务器通用的 ID 生成器。

Naming convention comes straight from ``DESIGN.md`` §10.1:
命名约定直接来自 ``DESIGN.md`` §10.1：

============== ============================
Resource       Format / 格式
============== ============================
request_id     ``req_<12 hex>``
trace_id       ``trace_<12 hex>``
chat id        ``chatcmpl-<12 hex>``
file id        ``file-<24 hex>``
batch id       ``batch_<24 hex>``
fine-tune id   ``ftjob-<24 hex>``
assistant id   ``asst_<24 hex>``
thread id      ``thread_<24 hex>``
run id         ``run_<24 hex>``
video id       ``video_<24 hex>``
character id   ``char_<12 hex>``
interaction id ``int_<12 hex>``
world id       ``world_<12 hex>``
memory id      ``mem_<12 hex>``
snapshot id    ``snap_<YYYYMMDD>_<6 hex>``
audit id       ``audit_<12 hex>``
challenge id   ``chal_<12 hex>``
session id     ``sess_<12 hex>``
message id     ``msg_<12 hex>``
import job id  ``imp_<12 hex>``
load op id     ``load_op_<12 hex>``
unload op id   ``unload_op_<12 hex>``
============== ============================

All generators use ``secrets.token_hex`` so they are crypto-grade and
collision-resistant.
所有生成器使用 ``secrets.token_hex``，因此达到加密级安全性且防碰撞。
"""

from __future__ import annotations

import datetime as _dt
import secrets

# Number of hex chars for short (12) and long (24) identifiers.
# 短 (12) 和长 (24) 标识符的十六进制字符数。
_SHORT_HEX_LEN = 12
_LONG_HEX_LEN = 24


def _hex(n: int) -> str:
    """Return ``n`` random hex characters using a crypto-grade RNG.
    使用加密级随机数生成器返回 ``n`` 个随机十六进制字符。"""
    # ``token_hex(n // 2)`` produces 2*n hex chars; if n is odd we'd round
    # down.  All callers pass even numbers, so this is safe.
    # ``token_hex(n // 2)`` 生成 2*n 个十六进制字符；若 n 为奇数会向下取整。所有调用者都传偶数，因此安全。
    return secrets.token_hex(n // 2)


def gen_id(prefix: str, length: int = _SHORT_HEX_LEN) -> str:
    """Return a string of the form ``"<prefix><length hex>"``.
    返回格式为 ``"<prefix><length hex>"`` 的字符串。

    Parameters / 参数
    ----------
    prefix:
        Resource prefix such as ``"req_"`` or ``"chatcmpl-"``.
        资源前缀，如 ``"req_"`` 或 ``"chatcmpl-"``。
    length:
        Number of hex characters after the prefix (12 or 24 typically).
        前缀后的十六进制字符数 (通常为 12 或 24)。
    """
    return f"{prefix}{_hex(length)}"


# --- Request / trace / 请求与追踪 ------------------------------------------------

def gen_request_id() -> str:
    """Return a new request id (``req_<12 hex>``).
    返回新的请求 ID (``req_<12 hex>``)。"""
    return gen_id("req_", _SHORT_HEX_LEN)


def gen_trace_id() -> str:
    """Return a new trace id (``trace_<12 hex>``).
    返回新的追踪 ID (``trace_<12 hex>``)。"""
    return gen_id("trace_", _SHORT_HEX_LEN)


# --- OAI-style resources / OpenAI 风格资源 -----------------------------------

def gen_chat_id() -> str:
    """Return a chat completion id (``chatcmpl-<12 hex>``).
    返回聊天补全 ID (``chatcmpl-<12 hex>``)。"""
    return gen_id("chatcmpl-", _SHORT_HEX_LEN)


def gen_file_id() -> str:
    """Return a file id (``file-<24 hex>``).
    返回文件 ID (``file-<24 hex>``)。"""
    return gen_id("file-", _LONG_HEX_LEN)


def gen_batch_id() -> str:
    """Return a batch id (``batch_<24 hex>``).
    返回批处理 ID (``batch_<24 hex>``)。"""
    return gen_id("batch_", _LONG_HEX_LEN)


def gen_fine_tuning_job_id() -> str:
    """Return a fine-tuning job id (``ftjob-<24 hex>``).
    返回微调任务 ID (``ftjob-<24 hex>``)。"""
    return gen_id("ftjob-", _LONG_HEX_LEN)


def gen_assistant_id() -> str:
    """Return an assistant id (``asst_<24 hex>``).
    返回助手 ID (``asst_<24 hex>``)。"""
    return gen_id("asst_", _LONG_HEX_LEN)


def gen_thread_id() -> str:
    """Return a thread id (``thread_<24 hex>``).
    返回线程 ID (``thread_<24 hex>``)。"""
    return gen_id("thread_", _LONG_HEX_LEN)


def gen_run_id() -> str:
    """Return a run id (``run_<24 hex>``).
    返回运行 ID (``run_<24 hex>``)。"""
    return gen_id("run_", _LONG_HEX_LEN)


def gen_video_id() -> str:
    """Return a video id (``video_<24 hex>``).
    返回视频 ID (``video_<24 hex>``)。"""
    return gen_id("video_", _LONG_HEX_LEN)


# --- XiJian extension resources / 隙间扩展资源 -----------------------------------

def gen_character_id() -> str:
    """Return a character id (``char_<12 hex>``).
    返回角色 ID (``char_<12 hex>``)。"""
    return gen_id("char_", _SHORT_HEX_LEN)


def gen_interaction_id() -> str:
    """Return an interaction id (``int_<12 hex>``).
    返回交互 ID (``int_<12 hex>``)。"""
    return gen_id("int_", _SHORT_HEX_LEN)


def gen_world_id() -> str:
    """Return a world id (``world_<12 hex>``).
    返回世界 ID (``world_<12 hex>``)。"""
    return gen_id("world_", _SHORT_HEX_LEN)


def gen_memory_id() -> str:
    """Return a memory entry id (``mem_<12 hex>``).
    返回记忆条目 ID (``mem_<12 hex>``)。"""
    return gen_id("mem_", _SHORT_HEX_LEN)


def gen_audit_id() -> str:
    """Return an audit id (``audit_<12 hex>``).
    返回审计 ID (``audit_<12 hex>``)。"""
    return gen_id("audit_", _SHORT_HEX_LEN)


def gen_challenge_id() -> str:
    """Return a challenge id (``chal_<12 hex>``).
    返回验证 ID (``chal_<12 hex>``)。"""
    return gen_id("chal_", _SHORT_HEX_LEN)


def gen_session_id() -> str:
    """Return a session id (``sess_<12 hex>``).
    返回会话 ID (``sess_<12 hex>``)。"""
    return gen_id("sess_", _SHORT_HEX_LEN)


def gen_message_id() -> str:
    """Return a message id (``msg_<12 hex>``).
    返回消息 ID (``msg_<12 hex>``)。"""
    return gen_id("msg_", _SHORT_HEX_LEN)


def gen_import_job_id() -> str:
    """Return an import job id (``imp_<12 hex>``).
    返回导入任务 ID (``imp_<12 hex>``)。"""
    return gen_id("imp_", _SHORT_HEX_LEN)


def gen_load_op_id() -> str:
    """Return a model load operation id (``load_op_<12 hex>``).
    返回模型加载操作 ID (``load_op_<12 hex>``)。"""
    return gen_id("load_op_", _SHORT_HEX_LEN)


def gen_unload_op_id() -> str:
    """Return a model unload operation id (``unload_op_<12 hex>``).
    返回模型卸载操作 ID (``unload_op_<12 hex>``)。"""
    return gen_id("unload_op_", _SHORT_HEX_LEN)


def gen_overload_event_id() -> str:
    """Return an overload event id (``overload_<12 hex>``).
    返回过载事件 ID (``overload_<12 hex>``)。"""
    return gen_id("overload_", _SHORT_HEX_LEN)


def gen_state_log_id() -> str:
    """Return a character state log id (``cstate_<12 hex>``).
    返回角色状态日志 ID (``cstate_<12 hex>``)。"""
    return gen_id("cstate_", _SHORT_HEX_LEN)


def gen_event_id() -> str:
    """Return a world event definition id (``event_<12 hex>``).
    返回世界事件定义 ID (``event_<12 hex>``)。"""
    return gen_id("event_", _SHORT_HEX_LEN)


def gen_event_instance_id() -> str:
    """Return a fired world-event instance id (``evinst_<12 hex>``).
    返回已触发的世界事件实例 ID (``evinst_<12 hex>``)。"""
    return gen_id("evinst_", _SHORT_HEX_LEN)


def gen_npc_id() -> str:
    """Return an NPC id (``npc_<12 hex>``).
    返回 NPC ID (``npc_<12 hex>``)。"""
    return gen_id("npc_", _SHORT_HEX_LEN)


def gen_npc_scheduling_log_id() -> str:
    """Return an NPC-tier-transition log id (``npcsched_<12 hex>``).
    返回 NPC 层级变迁日志 ID (``npcsched_<12 hex>``)。"""
    return gen_id("npcsched_", _SHORT_HEX_LEN)


def gen_world_audit_id() -> str:
    """Return a world-audit log id (``waudit_<12 hex>``).
    返回世界审计日志 ID (``waudit_<12 hex>``)。"""
    return gen_id("waudit_", _SHORT_HEX_LEN)


def gen_poi_id() -> str:
    """Return a point-of-interest id (``poi_<12 hex>``).
    返回兴趣点 ID (``poi_<12 hex>``)。

    A4.3 scene system: ``pois`` is a 3-level hierarchy (map / region /
    POI).  Each level shares this id format and is differentiated by
    the ``kind`` field on the record.
    A4.3 场景系统：``pois`` 为三级层次结构 (地图 / 区域 / 兴趣点)。
    每一级共享此 ID 格式，通过记录的 ``kind`` 字段区分。
    """
    return gen_id("poi_", _SHORT_HEX_LEN)


def gen_travel_mode_id() -> str:
    """Return a travel-mode id (``tmode_<12 hex>``).
    返回旅行模式 ID (``tmode_<12 hex>``)。

    A4.3 travel: per-world transport options like ``walk`` / ``horse``
    / ``teleport``.  Each option carries ``speed_factor``,
    ``stamina_cost`` and ``event_chance``.
    A4.3 旅行：每个世界的交通选项，如 ``walk`` / ``horse`` / ``teleport``。
    每个选项携带 ``speed_factor``、``stamina_cost`` 和 ``event_chance``。
    """
    return gen_id("tmode_", _SHORT_HEX_LEN)


def gen_scene_interaction_id() -> str:
    """Return a scene-interaction id (``sint_<12 hex>``).
    返回场景交互 ID (``sint_<12 hex>``)。

    A4.3 scene interactions: the user triggers an action against an
    NPC / object / mechanism at a POI.  Each definition carries a
    ``cooldown_sec`` so farming / exploitation is bounded.
    A4.3 场景交互：用户在兴趣点对 NPC/物体/机关触发动作。每个定义携带
    ``cooldown_sec`` 以限制刷/利用行为。

    The :func:`gen_interaction_id` helper above (prefix ``int_``) is
    for the chat-level interaction templates (拥抱/接吻) — different
    resource, different prefix.
    上方的 :func:`gen_interaction_id` 辅助函数 (前缀 ``int_``) 用于
    聊天级交互模板 (拥抱/接吻) — 不同资源，不同前缀。
    """
    return gen_id("sint_", _SHORT_HEX_LEN)


def gen_currency_id() -> str:
    """Return a currency id (``curr_<12 hex>``).
    返回货币 ID (``curr_<12 hex>``)。

    A4.4 economy: each per-world currency definition (``mora`` /
    ``credit`` / ``gold`` etc.) gets its own id.  The ``world_currencies``
    table is keyed on ``(world_id, code)`` — this id is the *internal*
    handle so admin tools can reference a currency record without
    round-tripping the natural key.
    A4.4 经济：每个世界的货币定义 (``mora`` / ``credit`` / ``gold`` 等) 获取其自身的 ID。
    ``world_currencies`` 表以 ``(world_id, code)`` 为键 — 此 ID 是*内部*句柄，
    使管理工具无需通过自然键往返即可引用货币记录。
    """
    return gen_id("curr_", _SHORT_HEX_LEN)


def gen_wallet_id() -> str:
    """Return a wallet id (``wlt_<12 hex>``).
    返回钱包 ID (``wlt_<12 hex>``)。

    A4.4 economy: a wallet is the (owner_kind, owner_id, world_id,
    currency_code) tuple materialized as a single record.  The id is
    the internal handle; the natural composite key is what callers
    use to look it up.
    A4.4 经济：钱包是 (owner_kind, owner_id, world_id, currency_code) 元组的具体记录。
    ID 为内部句柄；自然组合键是调用者用于查询的字段。
    """
    return gen_id("wlt_", _SHORT_HEX_LEN)


def gen_transaction_id() -> str:
    """Return a transaction id (``txn_<12 hex>``).
    返回交易 ID (``txn_<12 hex>``)。

    A4.4 economy: every money movement writes one record.  The id is
    the only mutable handle — wallets are looked up by composite key
    but every individual transaction is referenced by this id.
    A4.4 经济：每笔资金变动写入一条记录。ID 是唯一可变的句柄 —
    钱包通过组合键查找，但每笔独立交易通过此 ID 引用。
    """
    return gen_id("txn_", _SHORT_HEX_LEN)


def gen_economy_state_id() -> str:
    """Return an economy-state id (``eco_<12 hex>``).
    返回经济状态 ID (``eco_<12 hex>``)。

    A4.4 economy: there's at most one state record per world; the id
    is for the storage layer's convenience (the bucket is keyed on
    ``world_id`` but the id gives a stable handle for audit logs).
    A4.4 经济：每个世界最多有一条状态记录；ID 为存储层的便利而设
    (存储桶以 ``world_id`` 为键，但 ID 为审计日志提供稳定句柄)。
    """
    return gen_id("eco_", _SHORT_HEX_LEN)


def gen_safety_audit_id() -> str:
    """Return a safety-audit log id (``saf_<12 hex>``).
    返回安全审计日志 ID (``saf_<12 hex>``)。

    A5.1 output-safety: every scan (input pre-screen / output
    post-screen) lands one of these.  Operators query ``list_log``
    by id to answer "why did the safety layer block that?" — see
    AC-3 ("所有拦截事件必须可查询").
    A5.1 输出安全：每次扫描 (输入预检/输出后检) 产生一条记录。
    运维按 ID 查询 ``list_log`` 回答"安全层为何阻止该操作？"— 参见 AC-3。
    """
    return gen_id("saf_", _SHORT_HEX_LEN)


def gen_safety_rule_id() -> str:
    """Return a safety-rule id (``rule_<12 hex>``).
    返回安全规则 ID (``rule_<12 hex>``)。

    A5.1 output-safety: each rule is one of three flavours
    (``ooc_pattern`` / ``injection_pattern`` / ``forbidden_word``)
    and carries a 1..5 severity.  Inactive rules are skipped
    without being deleted so operators can A/B.
    A5.1 输出安全：每条规则为三种类型之一
    (``ooc_pattern`` / ``injection_pattern`` / ``forbidden_word``)，
    带 1..5 严重级别。非活跃规则被跳过但不删除，便于运维 A/B 测试。
    """
    return gen_id("rule_", _SHORT_HEX_LEN)


def gen_mcp_rule_id() -> str:
    """Return an MCP-rule id (``mcpr_<12 hex>``).
    返回 MCP 规则 ID (``mcpr_<12 hex>``)。

    A5.2 MCP-protection: the rulebook that the ``check()`` gate
    consults before any desktop-control action runs.  Each rule
    carries an ``action_kind`` (file_delete / file_write /
    file_read / shell / network / app_launch / settings_modify /
    system_cmd), a ``pattern`` (regex or literal depending on
    kind), a ``mode`` (blacklist / whitelist), and a
    1..5 severity.  The handle is ``mcpr_<12 hex>`` to keep it
    visually distinct from A5.1's ``rule_`` prefix.
    A5.2 MCP 防护：``check()`` 门禁在执行任何桌面控制操作前查阅的规则手册。
    每条规则携带 ``action_kind`` (file_delete / file_write / file_read / shell /
    network / app_launch / settings_modify / system_cmd)、``pattern``
    (正则或字面量，取决于类型)、``mode`` (黑名单/白名单) 和 1..5 严重级别。
    句柄为 ``mcpr_<12 hex>`` 以与 A5.1 的 ``rule_`` 前缀视觉区分。
    """
    return gen_id("mcpr_", _SHORT_HEX_LEN)


def gen_mcp_audit_id() -> str:
    """Return an MCP-audit id (``mcpa_<12 hex>``).
    返回 MCP 审计 ID (``mcpa_<12 hex>``)。

    A5.2 MCP-protection: every ``check()`` call lands one
    entry here so AC-1 ("黑名单动作 100% 拦截") is observable.
    Verdict is ``allowed`` / ``denied`` / ``denied_lockout`` /
    ``denied_crashed`` — the lockout / crashed variants carry
    the safety-stop state-machine reason so an operator
    looking at the log can tell "blocked because a rule
    matched" from "blocked because the system is in
    lockout".
    A5.2 MCP 防护：每次 ``check()`` 调用在此产生一条记录，使 AC-1 可观测。
    判决为 ``allowed`` / ``denied`` / ``denied_lockout`` / ``denied_crashed`` —
    lockout/crashed 变体携带安全停止状态机原因，使运维可从日志区分
    "因规则匹配被阻止"与"因系统锁定被阻止"。
    """
    return gen_id("mcpa_", _SHORT_HEX_LEN)


def gen_mcp_freeze_id() -> str:
    """Return a safety-stop (MCP freeze) id (``mcpf_<12 hex>``).
    返回安全停止 (MCP 冻结) ID (``mcpf_<12 hex>``)。

    A5.2 MCP-protection: every safety-stop the desktop client
    triggers (or that comes in via the ``POST /safety_stop``
    endpoint) lands a record here.  Status walks through
    ``frozen`` → ``awaiting_confirm`` → ``sanitizing`` →
    ``restored`` / ``cancelled`` / ``lockout``.  Three freezes
    within 60 s flip the world to ``lockout`` and refuse
    further freezes until cold restart.
    A5.2 MCP 防护：桌面客户端触发的每次安全停止 (或通过 ``POST /safety_stop``
    端点) 在此记录。状态经过 ``frozen`` → ``awaiting_confirm`` → ``sanitizing`` →
    ``restored`` / ``cancelled`` / ``lockout``。60 秒内三次冻结将世界切换为
    ``lockout`` 并拒绝进一步冻结直至冷重启。
    """
    return gen_id("mcpf_", _SHORT_HEX_LEN)


def gen_mcp_snapshot_id() -> str:
    """Return an MCP-snapshot id (``mcpsnap_<12 hex>``).
    返回 MCP 快照 ID (``mcpsnap_<12 hex>``)。

    A5.2 MCP-protection: the "专用备份文件夹" payload.  Every
    safety-stop dumps the world/character/memory/session bundle
    to one of these so the restore step can hydrate the live
    state from a known-good checkpoint.  The file path is
    server-controlled — the request never lands in
    ``file_path`` — so the request body can never escape the
    backup directory.
    A5.2 MCP 防护："专用备份文件夹"负载。每次安全停止将世界/角色/记忆/会话
    捆绑包转储到其中一个，使恢复步骤可从已知良好的检查点重建活动状态。
    文件路径由服务器控制 — 请求从不进入 ``file_path`` — 因此请求体绝不会逃逸备份目录。
    """
    return gen_id("mcpsnap_", _SHORT_HEX_LEN)


def gen_safety_snapshot_id() -> str:
    """Return an A5.3 safety-snapshot id (``sas_<12 hex>``).
    返回 A5.3 安全快照 ID (``sas_<12 hex>``)。

    A5.3 automatic backup: every scheduled / overload /
    safety_stop / manual snapshot lands one of these.
    Independent of A5.2's ``mcp_snapshots`` — the two
    buckets serve different purposes (A5.3 = "store and
    forget" archive, A5.2 = the safety-stop dump +
    sanitize + restore cycle).  See notes.md 2026-07-20
    for the split decision.
    A5.3 自动备份：每次计划/过载/安全停止/手动快照产生一条记录。
    独立于 A5.2 的 ``mcp_snapshots`` — 两个存储桶服务不同目的
    (A5.3 = "存储并遗忘"归档，A5.2 = 安全停止转储 + 清理 + 恢复循环)。
    拆分决策参见 notes.md 2026-07-20。
    """
    return gen_id("sas_", _SHORT_HEX_LEN)


def gen_backup_policy_id() -> str:
    """Return a backup-policy id (``bkpol_<12 hex>``).
    返回备份策略 ID (``bkpol_<12 hex>``)。

    A5.3 automatic backup: there's at most one policy
    record (id="default"); the id is the storage layer's
    handle so the route can PUT the policy without knowing
    the natural key.
    A5.3 自动备份：最多有一条策略记录 (id="default")；
    ID 为存储层的句柄，使路由无需知道自然键即可 PUT 策略。
    """
    return gen_id("bkpol_", _SHORT_HEX_LEN)


def gen_submission_id() -> str:
    """Return a Developer-Kit submission id (``sub_<12 hex>``).
    返回开发者套件提交 ID (``sub_<12 hex>``)。

    Used by :mod:`xijian_api.devkit` — every archive / SMTP submission
    gets its own short id so it can be referenced from the receiving
    side without leaking sensitive content into the local logs.
    供 :mod:`xijian_api.devkit` 使用 — 每次归档/SMTP 提交获取其简短 ID，
    以便在不将敏感内容泄露到本地日志的情况下从接收方引用。
    """
    return gen_id("sub_", _SHORT_HEX_LEN)


def gen_snapshot_id(now: _dt.datetime | None = None) -> str:
    """Return a snapshot id (``snap_<YYYYMMDD>_<6 hex>``).
    返回快照 ID (``snap_<YYYYMMDD>_<6 hex>``)。

    Parameters / 参数
    ----------
    now:
        Override the timestamp source (used for testing).  Defaults to
        :func:`datetime.datetime.now` in UTC.
        覆盖时间戳来源 (供测试使用)。默认为 UTC 的 :func:`datetime.datetime.now`。
    """
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = moment.strftime("%Y%m%d")
    return f"snap_{stamp}_{secrets.token_hex(3)}"
def gen_voice_call_id() -> str:
    """Return a voice-call id (``call_<12 hex>``).
    返回通话 ID (``call_<12 hex>``)。

    A6 realtime call: every call session (user- or character-
    initiated) gets one of these.  ``voice_calls`` records hang off
    this handle; ``call_events`` reference it via ``call_id``.
    A6 实时通话：每次通话会话 (用户发起或角色发起) 获取一个此类 ID。
    ``voice_calls`` 记录挂在此句柄下；``call_events`` 通过 ``call_id`` 引用它。
    """
    return gen_id("call_", _SHORT_HEX_LEN)


def gen_call_event_id() -> str:
    """Return a call-event id (``callevt_<12 hex>``).
    返回通话事件 ID (``callevt_<12 hex>``)。

    A6 realtime call: one event per speech / motion / effect / song
    milestone inside a call.  Kind is one of ``speech`` / ``motion`` /
    ``effect`` / ``song`` (plus lifecycle helpers such as
    ``barge_in``).
    A6 实时通话：通话内每个语音/动作/特效/唱歌里程碑一条事件。
    类型为 ``speech`` / ``motion`` / ``effect`` / ``song`` 之一
    (以及生命周期辅助类型，如 ``barge_in``)。
    """
    return gen_id("callevt_", _SHORT_HEX_LEN)


def gen_initiated_action_id() -> str:
    """Return a character-initiated action id (``init_<12 hex>``).
    返回角色主动发起动作 ID (``init_<12 hex>``)。

    A7 proactive contact: one record per character-initiated
    message / voice-call offer.  Status walks ``pending`` → ``sent``
    → ``accepted`` / ``declined`` / ``ignored``.
    A7 主动联系：每次角色主动发起的消息/来电邀约一条记录。
    状态经过 ``pending`` → ``sent`` → ``accepted`` / ``declined`` / ``ignored``。
    """
    return gen_id("init_", _SHORT_HEX_LEN)


def gen_desktop_pet_id() -> str:
    """Return a desktop-pet id (``pet_<12 hex>``).
    返回桌宠 ID (``pet_<12 hex>``)。

    A8 desktop pet: 1..N characters can roam the desktop; each
    placement is one record.  ``pet_action_log`` rows reference it
    via ``pet_id``.
    A8 桌宠：1~N 个角色可在桌面活动；每次放置一条记录。
    ``pet_action_log`` 行通过 ``pet_id`` 引用它。
    """
    return gen_id("pet_", _SHORT_HEX_LEN)


def gen_wallpaper_id() -> str:
    """Return a dynamic-wallpaper id (``wall_<12 hex>``).
    返回动态壁纸 ID (``wall_<12 hex>``)。

    A8 dynamic wallpaper: one active wallpaper per character at most;
    the record binds a character + world environment.  ID kept
    distinct from ``pet_`` so the two resource families never collide.
    A8 动态壁纸：每个角色最多一张活动壁纸；记录绑定角色 + 世界环境。
    ID 与 ``pet_`` 保持不同前缀，使两类资源永不相撞。
    """
    return gen_id("wall_", _SHORT_HEX_LEN)


def gen_pet_action_log_id() -> str:
    """Return a pet-action-log id (``petlog_<12 hex>``).
    返回桌宠动作日志 ID (``petlog_<12 hex>``)。

    A8 desktop pet: every auditable "捣乱" action (mouse_click /
    key_input / window_move / ...) lands one row here so AC-2
    ("桌宠捣乱必须有可审计日志") is satisfiable.
    A8 桌宠：每次可审计的"捣乱"动作 (mouse_click / key_input /
    window_move / ...) 在此落一条记录，使 AC-2 可满足。
    """
    return gen_id("petlog_", _SHORT_HEX_LEN)
