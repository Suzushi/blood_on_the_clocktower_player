"""
血染钟楼 - 玩家端 API
更新日期: 2026-01-12

此模块包含所有玩家端相关的API端点，实现玩家与说书人的双向通信。
"""

from flask import Blueprint, request, jsonify, render_template
from datetime import datetime
import threading
import time
import random
import secrets
from game_data import get_night_action_type

# 创建蓝图
player_bp = Blueprint('player', __name__)

# games 字典将从主应用传入
games = None
AUTO_MIN_NIGHT_SECONDS = 20
AUTO_NIGHT_ACTION_TIMEOUT = 45
AUTO_DAY_VOTE_SECONDS = 30
RECONNECT_TOKEN_TTL_SECONDS = 60 * 60 * 24

def init_player_api(games_dict):
    """初始化玩家API，传入games字典"""
    global games
    games = games_dict

def _issue_reconnect_token(player):
    token = secrets.token_urlsafe(32)
    player["reconnect_token"] = token
    player["reconnect_token_issued_at"] = time.time()
    player["reconnect_token_expires_at"] = time.time() + RECONNECT_TOKEN_TTL_SECONDS
    return token

def _is_reconnect_token_valid(player, token):
    if not player:
        return False
    saved = player.get("reconnect_token")
    expires_at = player.get("reconnect_token_expires_at", 0)
    if not saved or not token:
        return False
    if saved != token:
        return False
    if time.time() > float(expires_at):
        return False
    return True

def _extract_reconnect_token(payload=None):
    if isinstance(payload, dict):
        token = payload.get("reconnect_token")
        if token:
            return token
    return (
        request.args.get("reconnect_token")
        or request.headers.get("X-Player-Token")
        or request.headers.get("Authorization")
    )

def _require_player_auth(game, player_id, payload=None):
    player = _get_player(game, player_id)
    if not player:
        return None, (jsonify({"error": "无效的玩家"}), 400)
    reconnect_token = _extract_reconnect_token(payload)
    if not _is_reconnect_token_valid(player, reconnect_token):
        return None, (jsonify({"error": "玩家凭据无效或已过期，请重新加入房间"}), 401)
    return player, None

def _get_player(game, player_id):
    return next((p for p in game.players if p["id"] == player_id), None)

def _get_player_display_role_type(game, player):
    if not game or not player:
        return None
    role = player.get("role")
    if not role:
        return player.get("role_type")
    if hasattr(game, "_get_role_type"):
        return game._get_role_type(role)
    return player.get("role_type")

def _ensure_player_messages(player):
    if "messages" not in player:
        player["messages"] = []

def _push_player_message(game, player_id, message_type, title, content, extra=None):
    player = _get_player(game, player_id)
    if not player:
        return None
    _ensure_player_messages(player)
    message = {
        "id": f"auto_{datetime.now().timestamp()}",
        "type": message_type,
        "title": title,
        "content": content,
        "time": datetime.now().isoformat(),
        "read": False
    }
    if extra:
        message.update(extra)
    player["messages"].append(message)
    if len(player["messages"]) > 50:
        player["messages"] = player["messages"][-50:]
    return message["id"]

def _send_night_result_auto(game, player_id, info):
    result_type = info.get("info_type", "info") if isinstance(info, dict) else "info"
    result_data = info.get("message") if isinstance(info, dict) else str(info)
    _push_player_message(
        game,
        player_id,
        "night_result",
        "🌙 夜间信息",
        str(result_data),
        {"result_type": result_type, "result_data": result_data}
    )

def _can_skip_night_action(game, role_id, role_type):
    script_id = getattr(game, "script_id", "")
    if script_id == "trouble_brewing":
        return role_id == "spy" or role_id == "imp"
    return True

def _build_auto_action_config(game, player, role, action_type):
    role_id = role.get("id", "")
    role_type = role.get("type", "")
    config = {
        "max_targets": 1,
        "min_targets": 1,
        "unique_targets": True,
        "can_skip": _can_skip_night_action(game, role_id, role_type),
        "use_alive_only": True,
        "description": role.get("ability", "")
    }
    if action_type in ["kill", "assassin_kill", "poison", "drunk", "sailor_drunk", "grandchild_select", "butler_master", "exorcist", "devils_advocate", "pukka_poison", "zombuul_kill", "ability_select", "gambler_guess", "revive"]:
        config["can_select"] = True
        if action_type == "revive":
            config["target_scope"] = "dead"
    elif action_type == "protect":
        config["can_select"] = True
        if role_id == "innkeeper":
            config["max_targets"] = 2
    elif action_type == "shabaloth_kill":
        config["can_select"] = True
        config["max_targets"] = 2
    elif action_type == "po_kill":
        config["can_select"] = True
        config["max_targets"] = 3
    elif action_type == "pit_hag":
        config["can_select"] = True
        config["special"] = "pit_hag"
    elif action_type == "info_select":
        if role_id in ["empath", "undertaker", "oracle", "flowergirl"]:
            config["can_select"] = False
            config["is_info"] = True
            config["min_targets"] = 0
            config["max_targets"] = 0
        elif role_id == "spy":
            config["can_select"] = True
            config["min_targets"] = 0
            config["max_targets"] = 0
            config["description"] = "你可以查看本局当前日志，或选择跳过"
        else:
            config["can_select"] = True
            if role_id in ["fortune_teller", "chambermaid", "seamstress"]:
                config["max_targets"] = 2
                config["min_targets"] = 2
            if role_id == "fortune_teller":
                config["use_alive_only"] = False
                config["can_skip"] = False
    else:
        config["can_select"] = False
        config["min_targets"] = 0
        config["max_targets"] = 0
    return config

def _validate_targets_by_rules(targets, min_targets, max_targets, unique_targets):
    normalized = []
    for t in targets or []:
        try:
            normalized.append(int(t))
        except (TypeError, ValueError):
            return False, "目标参数无效", []
    if unique_targets and len(normalized) != len(set(normalized)):
        return False, "不能重复选择同一名玩家", []
    if min_targets is None:
        min_targets = 0
    if max_targets is None:
        max_targets = 0
    if len(normalized) < min_targets:
        if min_targets == 2:
            return False, "需要选择两名玩家", []
        return False, f"至少需要选择 {min_targets} 名玩家", []
    if max_targets >= 0 and len(normalized) > max_targets:
        return False, f"最多只能选择 {max_targets} 名玩家", []
    return True, "", normalized

def _validate_night_action_constraints(game, player, role_id, action_type, targets):
    player_id = player.get("id")
    for tid in targets:
        target_player = _get_player(game, tid)
        if not target_player:
            return False, "目标玩家不存在"
    if role_id == "monk" and action_type == "protect" and player_id in targets:
        return False, "僧侣不能选择自己"
    if role_id == "butler" and action_type == "butler_master":
        if player_id in targets:
            return False, "管家不能选择自己作为主人"
        for tid in targets:
            target_player = _get_player(game, tid)
            if not target_player or not target_player.get("alive", True):
                return False, "管家的主人必须是存活玩家"
    if role_id == "exorcist" and action_type == "exorcist" and targets:
        previous_targets = getattr(game, "exorcist_previous_targets", []) or []
        if previous_targets and previous_targets[-1] == targets[0]:
            return False, "驱魔人不能连续两晚选择同一名玩家"
    if role_id == "devils_advocate" and action_type == "devils_advocate" and targets:
        previous_targets = getattr(game, "devils_advocate_previous_targets", []) or []
        if previous_targets and previous_targets[-1] == targets[0]:
            return False, "恶魔代言人不能连续两晚选择同一名玩家"
    if role_id == "professor" and action_type == "revive":
        for tid in targets:
            target_player = _get_player(game, tid)
            if not target_player or target_player.get("alive", True):
                return False, "教授只能选择死亡玩家"
    return True, ""

def _create_pending_action(game, player, action_type, action_config):
    if not hasattr(game, 'pending_actions'):
        game.pending_actions = {}

    player_id = player["id"]
    alive_players = [
        {"id": p["id"], "name": p["name"]}
        for p in game.players
        if p.get("alive", True) and p["id"] != player_id
    ]
    all_players = [
        {"id": p["id"], "name": p["name"]}
        for p in game.players
        if p["id"] != player_id
    ]
    dead_players = [
        {"id": p["id"], "name": p["name"]}
        for p in game.players
        if not p.get("alive", True) and p["id"] != player_id
    ]
    all_players_with_self = [{"id": p["id"], "name": p["name"]} for p in game.players]

    role = player.get("role", {})
    role_id = role.get("id", "")
    role_name = role.get("name", "未知角色")
    include_self_roles = ["fortune_teller"]
    target_scope = action_config.get("target_scope")
    if target_scope == "dead":
        target_list = dead_players
    elif target_scope == "all":
        target_list = all_players
    elif target_scope == "all_with_self":
        target_list = all_players_with_self
    elif role_id in include_self_roles and not action_config.get("use_alive_only", True):
        target_list = all_players_with_self
    elif action_config.get("use_alive_only", True):
        target_list = alive_players
    else:
        target_list = all_players

    pending_action = {
        "player_id": player_id,
        "player_name": player["name"],
        "role_id": role_id,
        "role_name": role_name,
        "action_type": action_type,
        "phase": game.current_phase,
        "config": action_config,
        "targets": target_list,
        "min_targets": action_config.get("min_targets", 1 if action_config.get("can_select") else 0),
        "max_targets": action_config.get("max_targets", 1),
        "unique_targets": action_config.get("unique_targets", True),
        "can_skip": action_config.get("can_skip", True),
        "description": action_config.get("description", role.get("ability", "")),
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "choice": None
    }

    game.pending_actions[player_id] = pending_action
    if hasattr(game, 'player_night_choices') and player_id in game.player_night_choices:
        del game.player_night_choices[player_id]
    game.add_log(f"[系统] 等待 {player['name']} ({role_name}) 进行行动选择", "info")
    return pending_action

def _send_first_night_intro(game):
    game.reconcile_player_role_types("首夜引导")
    minions = []
    demons = []
    for p in game.players:
        actual_role = game._get_player_actual_role(p) if hasattr(game, "_get_player_actual_role") else p.get("role")
        actual_role_type = game._get_role_type(actual_role) if actual_role else None
        if actual_role_type == "minion":
            minions.append(p)
        elif actual_role_type == "demon":
            demons.append(p)
    in_play_role_ids = set()
    for p in game.players:
        role = p.get("true_role") if p.get("is_the_drunk") and p.get("true_role") else p.get("role")
        if role:
            in_play_role_ids.add(role.get("id"))

    bluff_pool = []
    for role_type in ["townsfolk", "outsider"]:
        for role in game.script["roles"].get(role_type, []):
            if role["id"] not in in_play_role_ids:
                bluff_pool.append(role)
    random.shuffle(bluff_pool)
    bluff_roles = bluff_pool[:3]
    bluff_text = "、".join([r["name"] for r in bluff_roles]) if bluff_roles else "无"
    minion_text = "、".join([m["name"] for m in minions]) if minions else "无"
    demon_text = "、".join([d["name"] for d in demons]) if demons else "无"

    for demon in demons:
        content = f"你的爪牙是：{minion_text}。\n你的三个伪装身份建议是：{bluff_text}。"
        _push_player_message(game, demon["id"], "night_result", "🌙 首夜恶魔信息", content)
    for minion in minions:
        content = f"恶魔是：{demon_text}。"
        _push_player_message(game, minion["id"], "night_result", "🌙 首夜爪牙信息", content)

def _apply_auto_night_choice(game, player, role, action_type, choice):
    targets = choice.get("targets", []) if isinstance(choice, dict) else []
    extra_data = choice.get("extra_data", {}) if isinstance(choice, dict) else {}
    skipped = bool(choice.get("skipped", False)) if isinstance(choice, dict) else True
    target = targets[0] if targets else None

    if skipped:
        game.record_night_action(player["id"], role.get("name", ""), None, "跳过", "skip")
        return

    if action_type == "info_select":
        info = game.generate_info(player["id"], role.get("id"), targets)
        if info:
            _send_night_result_auto(game, player["id"], info)
            game.record_night_action(player["id"], role.get("name", ""), target, info.get("message", "信息已发送"), "info", extra_data)
        else:
            game.record_night_action(player["id"], role.get("name", ""), target, "无可生成信息", "info", extra_data)
        return

    if action_type == "protect" and role.get("id") == "innkeeper" and len(targets) >= 2:
        extra_data = extra_data or {}
        extra_data.setdefault("second_target", targets[1])
        extra_data.setdefault("drunk_target", random.choice([targets[0], targets[1]]))
    if action_type == "shabaloth_kill" and len(targets) >= 2:
        extra_data = extra_data or {}
        extra_data.setdefault("second_target", targets[1])
    if action_type == "po_kill" and len(targets) >= 2:
        extra_data = extra_data or {}
        extra_data.setdefault("targets", targets)

    game.record_night_action(player["id"], role.get("name", ""), target, "已完成", action_type, extra_data)

def _run_auto_night_loop(game):
    try:
        if getattr(game, "auto_night_running", False):
            return
        game.auto_night_running = True
        game.auto_night_started_at = time.time()
        game.current_night_index = 0
        game.pending_actions = {}
        if not hasattr(game, 'player_night_choices'):
            game.player_night_choices = {}

        if game.night_number == 1:
            _send_first_night_intro(game)

        auto_info_roles = {"washerwoman", "librarian", "investigator", "chef", "clockmaker", "empath", "undertaker", "oracle", "flowergirl"}
        night_order = game.get_night_order()
        for idx, item in enumerate(night_order):
            if game.current_phase != "night":
                break
            game.current_night_index = idx
            player = item["player"]
            role = item["role"]
            role_id = role.get("id", "")
            role_type = game._get_role_type(role)
            action_type = get_night_action_type(role_id, role_type)

            if role_id in auto_info_roles:
                info = game.generate_info(player["id"], role_id, None)
                if info:
                    _send_night_result_auto(game, player["id"], info)
                    game.record_night_action(player["id"], role.get("name", ""), None, info.get("message", "信息已发送"), "info")
                continue

            action_config = _build_auto_action_config(game, player, role, action_type)
            if not action_config.get("can_select"):
                continue

            _create_pending_action(game, player, action_type, action_config)
            _push_player_message(game, player["id"], "info", "🌙 轮到你行动", "请在夜间行动面板完成你的技能选择。")

            choice = {"targets": [], "extra_data": {}, "skipped": True}
            start_wait = time.time()
            while time.time() - start_wait < AUTO_NIGHT_ACTION_TIMEOUT:
                if game.current_phase != "night":
                    break
                pending = getattr(game, "pending_actions", {}).get(player["id"])
                if pending and pending.get("status") == "submitted":
                    choice = pending.get("choice") or choice
                    break
                time.sleep(1)

            _apply_auto_night_choice(game, player, role, action_type, choice)

            if player["id"] in game.player_night_choices:
                game.player_night_choices[player["id"]]["confirmed"] = True
            if player["id"] in game.pending_actions:
                game.pending_actions[player["id"]]["status"] = "confirmed"

        elapsed = time.time() - game.auto_night_started_at
        min_night = getattr(game, "min_night_duration", AUTO_MIN_NIGHT_SECONDS)
        if game.current_phase == "night" and elapsed < min_night:
            time.sleep(min_night - elapsed)

        if game.current_phase == "night":
            game.start_day()
            game.check_game_end(apply_scarlet_woman=True)
    finally:
        game.auto_night_running = False

def _start_auto_night_loop(game):
    thread = threading.Thread(target=_run_auto_night_loop, args=(game,), daemon=True)
    thread.start()
    game.auto_night_thread = thread

def _build_votes_detail(nomination):
    votes = nomination.get("votes", [])
    votes_detail = nomination.get("votes_detail")
    if votes_detail:
        return votes_detail
    detail = {}
    for v in votes:
        voter_id = v.get("voter_id")
        if voter_id is None:
            continue
        detail[voter_id] = {
            "player_name": v.get("voter_name"),
            "vote": v.get("vote"),
            "is_alive": v.get("voter_alive", True)
        }
    return detail

def _serialize_nomination(nomination):
    votes = nomination.get("votes", [])
    voters = nomination.get("voters")
    if voters is None:
        voters = [v.get("voter_id") for v in votes if v.get("voter_id") is not None]
    return {
        "id": nomination["id"],
        "nominator_id": nomination.get("nominator_id"),
        "nominator_name": nomination["nominator_name"],
        "nominee_id": nomination.get("nominee_id"),
        "nominee_name": nomination["nominee_name"],
        "status": nomination.get("status", "pending"),
        "vote_count": nomination.get("vote_count", 0),
        "voters": voters,
        "votes_detail": _build_votes_detail(nomination),
        "vote_started_at": nomination.get("vote_started_at"),
        "vote_deadline_at": nomination.get("vote_deadline_at"),
        "day_result": nomination.get("day_result"),
        "required_votes": nomination.get("required_votes")
    }

def _is_owner(game, owner_token):
    expected_token = getattr(game, 'owner_token', None)
    return bool(expected_token and owner_token == expected_token)

def _get_eligible_voter_ids(game):
    eligible = []
    for p in game.players:
        if p.get("alive", True) or p.get("vote_token", False):
            eligible.append(p["id"])
    return eligible

def _get_active_voting_nomination(game):
    return next((n for n in game.nominations if n.get("status") == "voting"), None)

def _required_votes(game):
    alive_count = len([p for p in game.players if p.get("alive", True)])
    return (alive_count // 2) + 1

def _close_nomination_if_active(game, nomination, reason="timeout"):
    if not nomination or nomination.get("status") != "voting":
        return {"success": False, "error": "当前没有进行中的投票"}
    nomination["status"] = "closed"
    nomination["closed_reason"] = reason
    required_votes = _required_votes(game)
    vote_count = nomination.get("vote_count", 0)
    nomination["required_votes"] = required_votes

    if vote_count < required_votes:
        nomination["day_result"] = "insufficient"
        game.add_log(f"[系统] 提名 #{nomination['id']} 投票结束（未达门槛）", "execution")
        return {"success": True, "closed": True, "on_the_block": False}

    if vote_count > getattr(game, "day_leading_vote_count", 0):
        prev_id = getattr(game, "day_leading_nomination_id", None)
        if prev_id:
            prev_nom = next((n for n in game.nominations if n["id"] == prev_id), None)
            if prev_nom:
                prev_nom["day_result"] = "behind"
        game.day_leading_nomination_id = nomination["id"]
        game.day_leading_vote_count = vote_count
        game.day_tied = False
        nomination["day_result"] = "leading"
        game.add_log(f"[系统] 提名 #{nomination['id']} 成为当前待处决（{vote_count} 票）", "execution")
        return {"success": True, "closed": True, "on_the_block": True, "tied": False}

    if vote_count == getattr(game, "day_leading_vote_count", 0):
        prev_id = getattr(game, "day_leading_nomination_id", None)
        if prev_id:
            prev_nom = next((n for n in game.nominations if n["id"] == prev_id), None)
            if prev_nom:
                prev_nom["day_result"] = "tied"
        game.day_leading_nomination_id = None
        game.day_tied = True
        nomination["day_result"] = "tied"
        game.add_log(f"[系统] 出现平票（{vote_count} 票），当前无人待处决", "execution")
        return {"success": True, "closed": True, "on_the_block": False, "tied": True}

    nomination["day_result"] = "behind"
    return {"success": True, "closed": True, "on_the_block": False}

def _end_day_and_start_night(game):
    leading_id = getattr(game, "day_leading_nomination_id", None)
    tied = bool(getattr(game, "day_tied", False))
    execution_result = {"executed": False}
    
    if leading_id and not tied:
        execution_result = game.execute(leading_id)
        if not execution_result.get("success"):
            return {"success": False, "error": execution_result.get("error", "结算失败")}
    else:
        game.add_log("[系统] 白天结束：无人被处决", "execution")

    tinker_result = game._roll_tinker_death("白天结束")
    final_game_end = game.check_game_end(apply_scarlet_woman=True, allow_mayor_day_end=True)
    
    if final_game_end.get("ended"):
        return {"success": True, "execution_result": execution_result, "tinker_result": tinker_result, "game_end": final_game_end, "started_night": False}

    game.start_night()
    _start_auto_night_loop(game)
    return {
        "success": True,
        "execution_result": execution_result,
        "tinker_result": tinker_result,
        "game_end": {"ended": False},
        "started_night": True,
        "night_number": game.night_number
    }

def _start_vote_timeout_loop(game, nomination_id):
    def _run():
        while True:
            time.sleep(1)
            nomination = next((n for n in game.nominations if n["id"] == nomination_id), None)
            if not nomination or nomination.get("status") != "voting":
                return
            deadline = nomination.get("vote_deadline_at", 0)
            if deadline and time.time() >= deadline:
                _close_nomination_if_active(game, nomination, "timeout")
                return
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

def _submit_pending_action_choice(game, player, pending, targets, extra_data=None, skipped=False):
    extra_data = extra_data or {}
    target_names = []
    for tid in targets:
        target_player = next((p for p in game.players if p["id"] == tid), None)
        if target_player:
            target_names.append(target_player["name"])

    pending["status"] = "submitted"
    pending["choice"] = {
        "targets": targets,
        "target_names": target_names,
        "extra_data": extra_data,
        "skipped": skipped,
        "submitted_at": datetime.now().isoformat()
    }

    if not hasattr(game, 'player_night_choices'):
        game.player_night_choices = {}

    game.player_night_choices[player["id"]] = {
        "player_id": player["id"],
        "player_name": player["name"],
        "role_id": pending["role_id"],
        "role_name": pending["role_name"],
        "targets": targets,
        "target_names": target_names,
        "extra_data": extra_data,
        "skipped": skipped,
        "submitted_at": datetime.now().isoformat(),
        "confirmed": False
    }

    if skipped:
        game.add_log(f"[玩家选择] {player['name']} ({pending['role_name']}) 选择跳过行动", "player_action")
    else:
        if target_names:
            game.add_log(f"[玩家选择] {player['name']} ({pending['role_name']}) 选择了 {', '.join(target_names)}", "player_action")
        elif pending.get("role_id") == "spy":
            game.add_log(f"[玩家选择] {player['name']} ({pending['role_name']}) 执行了日志查看", "player_action")
        else:
            game.add_log(f"[玩家选择] {player['name']} ({pending['role_name']}) 提交了无目标行动", "player_action")


def _create_day_pending_action(game, player, config):
    if not hasattr(game, 'pending_actions'):
        game.pending_actions = {}
    action = {
        "player_id": player["id"],
        "player_name": player["name"],
        "role_id": (player.get("role") or {}).get("id", ""),
        "role_name": (player.get("role") or {}).get("name", "未知角色"),
        "action_type": "day_action",
        "phase": game.current_phase,
        "config": config,
        "targets": config.get("targets", []),
        "min_targets": config.get("min_targets", 1),
        "max_targets": config.get("max_targets", 1),
        "unique_targets": config.get("unique_targets", True),
        "can_skip": config.get("can_skip", False),
        "description": config.get("description", ""),
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "choice": None
    }
    game.pending_actions[player["id"]] = action
    return action


def _get_or_create_day_action(game, player):
    pending_actions = getattr(game, 'pending_actions', {})
    pending = pending_actions.get(player["id"])
    if pending and pending.get("status") in {"pending", "submitted"} and pending.get("action_type") == "day_action":
        return pending

    role_id = (player.get("role") or {}).get("id")
    if role_id == "moonchild" and getattr(game, "pending_moonchild", None) == player["id"]:
        targets = [
            {"id": p["id"], "name": p["name"]}
            for p in game.players
            if p["id"] != player["id"] and p.get("alive", True)
        ]
        return _create_day_pending_action(game, player, {
            "action_name": "月之子选择",
            "description": "选择一名玩家。若其与你同阵营，则其死亡。",
            "targets": targets,
            "min_targets": 1,
            "max_targets": 1,
            "unique_targets": True,
            "can_skip": False
        })

    return None


# ==================== 页面路由 ====================

@player_bp.route('/player')
def player_page():
    """玩家端页面"""
    return render_template('player.html')


# ==================== 游戏连接 API ====================

@player_bp.route('/api/player/find_game/<game_code>', methods=['GET'])
def find_game_by_code(game_code):
    """通过游戏代码查找游戏"""
    game_code = game_code.strip()
    
    # 尝试直接匹配
    if game_code in games:
        game = games[game_code]
        players = [{
            "id": p["id"],
            "name": p["name"],
            "connected": p.get("connected", False)
        } for p in game.players]
        
        return jsonify({
            "found": True,
            "game_id": game_code,
            "script_name": game.script["name"],
            "players": players,
            "player_count": game.player_count
        })
    
    # 尝试部分匹配（游戏ID的后半部分）
    for gid, game in games.items():
        if game_code in gid or gid.endswith(game_code):
            players = [{
                "id": p["id"],
                "name": p["name"],
                "connected": p.get("connected", False)
            } for p in game.players]
            
            return jsonify({
                "found": True,
                "game_id": gid,
                "script_name": game.script["name"],
                "players": players,
                "player_count": game.player_count
            })
    
    return jsonify({"found": False})


@player_bp.route('/api/player/join_game', methods=['POST'])
def player_join_game():
    """玩家加入游戏"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player = next((p for p in game.players if p["id"] == player_id), None)
    
    if not player:
        return jsonify({"error": "无效的玩家"}), 400
    
    if player.get("connected"):
        return jsonify({"error": "该座位已被占用"}), 400
    
    # 标记玩家已连接
    player["connected"] = True
    player["last_seen"] = datetime.now().isoformat()
    reconnect_token = _issue_reconnect_token(player)
    
    # 初始化玩家消息队列
    if "messages" not in player:
        player["messages"] = []
    
    return jsonify({
        "success": True,
        "player_name": player["name"],
        "role": player.get("role"),
        "role_type": player.get("role_type"),
        "display_role_type": _get_player_display_role_type(game, player),
        "alive": player.get("alive", True),
        "reconnect_token": reconnect_token,
        "reconnect_token_ttl_seconds": RECONNECT_TOKEN_TTL_SECONDS
    })


@player_bp.route('/api/player/reconnect', methods=['POST'])
def player_reconnect():
    """玩家重新连接"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    reconnect_token = data.get('reconnect_token')
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在", "success": False}), 404
    
    game = games[game_id]
    player = next((p for p in game.players if p["id"] == player_id), None)
    
    if not player:
        return jsonify({"error": "无效的玩家", "success": False}), 400
    if not _is_reconnect_token_valid(player, reconnect_token):
        return jsonify({"error": "重连凭据无效或已过期，请重新加入房间", "success": False}), 401
    
    # 重新标记连接
    player["connected"] = True
    player["last_seen"] = datetime.now().isoformat()
    new_reconnect_token = _issue_reconnect_token(player)
    
    # 返回完整游戏状态
    players_public = [{
        "id": p["id"],
        "name": p["name"],
        "alive": p.get("alive", True),
        "appears_dead": p.get("appears_dead", False)
    } for p in game.players]
    
    return jsonify({
        "success": True,
        "player_name": player["name"],
        "role": player.get("role"),
        "role_type": player.get("role_type"),
        "display_role_type": _get_player_display_role_type(game, player),
        "alive": player.get("alive", True),
        "current_phase": game.current_phase,
        "day_number": game.day_number,
        "night_number": game.night_number,
        "players": players_public,
        "reconnect_token": new_reconnect_token,
        "reconnect_token_ttl_seconds": RECONNECT_TOKEN_TTL_SECONDS
    })


@player_bp.route('/api/player/start_game', methods=['POST'])
def player_start_game():
    """房主开始游戏（进入首夜）"""
    data = request.json
    game_id = data.get('game_id')
    owner_token = data.get('owner_token')

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    if not _is_owner(game, owner_token):
        return jsonify({"error": "仅房主可开始游戏"}), 403

    if game.current_phase != "setup":
        return jsonify({"error": "游戏已开始"}), 400

    game.start_night()
    game.current_night_index = 0
    night_order = game.get_night_order()
    _start_auto_night_loop(game)

    return jsonify({
        "success": True,
        "current_phase": game.current_phase,
        "night_number": game.night_number,
        "night_order_count": len(night_order),
        "message": "房主已开始游戏，进入首夜"
    })


# ==================== 游戏状态 API ====================

@player_bp.route('/api/player/game_state/<game_id>/<int:player_id>', methods=['GET'])
def get_player_game_state(game_id, player_id):
    """获取玩家视角的游戏状态（包含同步信息）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    if hasattr(game, "reconcile_player_role_types"):
        game.reconcile_player_role_types("状态查询")
    player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error
    
    # 更新最后在线时间
    player["last_seen"] = datetime.now().isoformat()
    
    # 公开的玩家信息
    players_public = [{
        "id": p["id"],
        "name": p["name"],
        "alive": p.get("alive", True) and not p.get("appears_dead", False),
        "connected": p.get("connected", False)
    } for p in game.players]
    
    # 公开日志
    public_log = [
        log for log in game.game_log 
        if log["type"] in ["phase", "death", "execution", "game_end", "game_event", "vote"]
    ]
    
    # 当前活跃的提名
    active_nomination = None
    for nom in game.nominations:
        if nom.get("status") == "voting":
            active_nomination = _serialize_nomination(nom)
            deadline = active_nomination.get("vote_deadline_at")
            if deadline:
                active_nomination["vote_remaining_sec"] = max(0, int(deadline - time.time()))
            break
    
    # 获取玩家的未读消息（来自说书人的信息）
    messages = player.get("messages", [])
    unread_messages = [m for m in messages if not m.get("read")]
    
    # 检查夜间行动
    my_turn = False
    night_action = None
    waiting_for_action = False
    
    if game.current_phase == "night":
        night_order = game.get_night_order()
        current_index = getattr(game, 'current_night_index', 0)
        
        # 检查是否在夜间行动序列中
        for i, action in enumerate(night_order):
            if action["player"]["id"] == player_id:
                if i == current_index:
                    my_turn = True
                elif i > current_index:
                    waiting_for_action = True
                break
        
        if my_turn:
            role_id = player.get("role", {}).get("id", "")
            role_type = player.get("role_type", "")
            
            # 确定行动类型和可选目标
            action_config = get_night_action_config(role_id, role_type, game, player_id)
            night_action = action_config
    
    # 检查玩家是否已提交夜间选择
    player_choice = None
    if hasattr(game, 'player_night_choices') and player_id in game.player_night_choices:
        player_choice = game.player_night_choices[player_id]
    
    # 检查游戏结束
    game_end = game.check_game_end() if hasattr(game, 'check_game_end') else None
    leading_nomination_id = getattr(game, "day_leading_nomination_id", None)
    leading_nomination = next((n for n in game.nominations if n["id"] == leading_nomination_id), None) if leading_nomination_id else None
    day_vote_state = {
        "leading_nomination_id": leading_nomination_id,
        "leading_nominee_name": leading_nomination.get("nominee_name") if leading_nomination else None,
        "leading_vote_count": getattr(game, "day_leading_vote_count", 0),
        "tied": bool(getattr(game, "day_tied", False))
    }
    
    return jsonify({
        "players": players_public,
        "current_phase": game.current_phase,
        "day_number": game.day_number,
        "night_number": game.night_number,
        "nominations": [_serialize_nomination(n) for n in game.nominations],
        "active_nomination": active_nomination,
        "day_vote_state": day_vote_state,
        "my_status": {
            "alive": player.get("alive", True),
            "vote_token": player.get("vote_token", True),
            "role": player.get("role"),
            "role_type": player.get("role_type"),
            "display_role_type": _get_player_display_role_type(game, player),
            "drunk": player.get("drunk", False),
            "poisoned": player.get("poisoned", False)
        },
        "my_turn": my_turn,
        "waiting_for_action": waiting_for_action,
        "night_action": night_action,
        "player_choice": player_choice,
        "messages": unread_messages,
        "public_log": public_log[-30:],  # 最近30条
        "game_end": game_end
    })


def get_night_action_config(role_id, role_type, game, player_id):
    """获取夜间行动配置"""
    alive_players = [p for p in game.players if p.get("alive", True) and p["id"] != player_id]
    all_players = [p for p in game.players if p["id"] != player_id]
    all_players_with_self = [{"id": p["id"], "name": p["name"]} for p in game.players]
    
    config = {
        "type": "other",
        "role_id": role_id,
        "can_select": False,
        "can_skip": _can_skip_night_action(game, role_id, role_type),
        "targets": [],
        "min_targets": 0,
        "max_targets": 1,
        "unique_targets": True,
        "description": ""
    }
    
    is_bmr = getattr(game, "script_id", "") == "bad_moon_rising"
    if role_type == "demon":
        config["type"] = "kill"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名玩家击杀"
        if is_bmr and role_id == "zombuul":
            config["type"] = "zombuul_kill"
            config["min_targets"] = 0
            config["description"] = "若今天白天无人死亡，可选择一名玩家击杀（可跳过）"
        elif is_bmr and role_id == "shabaloth":
            config["type"] = "shabaloth_kill"
            config["max_targets"] = 2
            config["description"] = "选择最多两名玩家击杀，可选择复活一名死亡玩家"
        elif is_bmr and role_id == "po":
            config["type"] = "po_kill"
            config["min_targets"] = 0
            config["max_targets"] = 3
            config["description"] = "可选择不杀；若上一晚未杀，本晚可选择三名目标"
        elif is_bmr and role_id == "pukka":
            config["type"] = "pukka_poison"
            config["description"] = "选择一名玩家中毒，上一位中毒目标在今晚死亡"
    
    elif role_id == "monk":
        config["type"] = "protect"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名玩家保护"
    
    elif role_id == "poisoner":
        config["type"] = "poison"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名玩家下毒"
    
    elif role_id == "fortune_teller":
        config["type"] = "fortune_tell"
        config["can_select"] = True
        config["can_skip"] = False
        config["targets"] = all_players_with_self
        config["min_targets"] = 2
        config["max_targets"] = 2
        config["description"] = "选择两名玩家查验是否有恶魔"
    
    elif role_id == "empath":
        config["type"] = "info"
        config["can_select"] = False
        config["description"] = "等待说书人告知你邻座的邪恶玩家数量"
    
    elif role_id == "undertaker":
        config["type"] = "info"
        config["can_select"] = False
        config["description"] = "等待说书人告知昨天被处决玩家的角色"
    
    elif role_id == "ravenkeeper":
        config["type"] = "investigate"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in all_players]
        config["description"] = "选择一名玩家查验其角色"
    
    elif role_id == "slayer":
        config["type"] = "day_ability"
        config["can_select"] = False
        config["description"] = "你的能力在白天使用"
    
    elif role_id == "butler":
        config["type"] = "choose_master"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择你的主人（只能跟随主人投票）"
    
    elif role_id == "spy":
        config["type"] = "info_select"
        config["can_select"] = True
        config["min_targets"] = 0
        config["max_targets"] = 0
        config["description"] = "你可以查看本局当前日志，或选择跳过"
    elif role_id == "grandmother":
        config["type"] = "grandchild_select"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in all_players]
        config["description"] = "选择一名玩家作为你的孙子"
    elif role_id == "sailor":
        config["type"] = "sailor_drunk"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名存活玩家，你们其中一人醉酒到明天黄昏"
    elif role_id == "innkeeper":
        config["type"] = "protect"
        config["can_select"] = True
        config["min_targets"] = 2
        config["max_targets"] = 2
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择两名玩家保护，其中一人会醉酒"
    elif role_id == "exorcist":
        config["type"] = "exorcist"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名玩家；若是恶魔则其今晚无法行动"
    elif role_id == "devils_advocate":
        config["type"] = "devils_advocate"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名存活玩家，若其明天被处决则不会死亡"
    elif role_id == "assassin":
        config["type"] = "assassin_kill"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "每局一次，夜晚选择一名玩家：其死亡（无视保护）"
    elif role_id == "gambler":
        config["type"] = "gambler_guess"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "选择一名玩家并猜测其角色；若猜错则你死亡"
    elif role_id == "courtier":
        config["type"] = "drunk"
        config["can_select"] = True
        config["min_targets"] = 1
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in alive_players]
        config["description"] = "每局一次，选择一名玩家，其醉酒 3 天 3 夜"
    elif role_id == "professor":
        config["type"] = "revive"
        config["can_select"] = True
        config["min_targets"] = 1
        config["use_alive_only"] = False
        config["targets"] = [{"id": p["id"], "name": p["name"]} for p in game.players if not p.get("alive", True)]
        config["description"] = "每局一次，选择一名死亡玩家使其复活"
    
    elif role_id in ["washerwoman", "librarian", "investigator", "chef", "clockmaker"]:
        config["type"] = "info"
        config["can_select"] = False
        config["description"] = "等待说书人提供首夜信息"
    
    else:
        config["type"] = "no_action"
        config["description"] = "你今晚没有行动"
    
    return config


# ==================== 玩家行动 API ====================

@player_bp.route('/api/player/night_action', methods=['POST'])
def player_night_action():
    """玩家提交夜间行动选择（同步到说书人端）"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    targets = data.get('targets', [])
    action_type = data.get('action_type')
    extra_data = data.get('extra_data', {})
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error

    role = player.get("true_role") if player.get("is_the_drunk") and player.get("true_role") else player.get("role", {})
    role_id = role.get("id", "")
    role_type = game._get_role_type(role)
    config = get_night_action_config(role_id, role_type, game, player_id)
    min_targets = config.get("min_targets", 1 if config.get("can_select") else 0)
    max_targets = config.get("max_targets", 1 if config.get("can_select") else 0)
    unique_targets = config.get("unique_targets", True)
    ok, err, normalized_targets = _validate_targets_by_rules(targets, min_targets, max_targets, unique_targets)
    if not ok and not extra_data.get("skipped", False):
        return jsonify({"error": err}), 400
    if extra_data.get("skipped", False):
        normalized_targets = []
    else:
        ok, err = _validate_night_action_constraints(game, player, role_id, action_type, normalized_targets)
        if not ok:
            return jsonify({"error": err}), 400
    
    # 初始化玩家夜间选择存储
    if not hasattr(game, 'player_night_choices'):
        game.player_night_choices = {}
    
    # 记录玩家的选择
    choice = {
        "player_id": player_id,
        "player_name": player["name"],
        "role_id": player.get("role", {}).get("id", ""),
        "role_name": player.get("role", {}).get("name", ""),
        "targets": normalized_targets,
        "action_type": action_type,
        "extra_data": extra_data,
        "submitted_at": datetime.now().isoformat(),
        "confirmed": False  # 等待说书人确认
    }
    
    # 添加目标名称
    target_names = []
    for tid in normalized_targets:
        target_player = next((p for p in game.players if p["id"] == tid), None)
        if target_player:
            target_names.append(target_player["name"])
    choice["target_names"] = target_names
    
    game.player_night_choices[player_id] = choice
    
    if extra_data.get("skipped", False):
        game.add_log(f"[玩家选择] {player['name']} ({choice['role_name']}) 选择跳过行动", "player_action")
    elif normalized_targets:
        game.add_log(f"[玩家选择] {player['name']} ({choice['role_name']}) 选择了 {', '.join(target_names)}", "player_action")
    elif choice.get("role_id") == "spy":
        game.add_log(f"[玩家选择] {player['name']} ({choice['role_name']}) 执行了日志查看", "player_action")
    else:
        game.add_log(f"[玩家选择] {player['name']} ({choice['role_name']}) 提交了无目标行动", "player_action")
    
    return jsonify({
        "success": True,
        "message": "选择已提交，等待说书人处理",
        "choice": choice
    })


@player_bp.route('/api/player/nominate', methods=['POST'])
def player_nominate():
    """玩家提名"""
    data = request.json
    game_id = data.get('game_id')
    nominator_id = data.get('nominator_id')
    nominee_id = data.get('nominee_id')
    try:
        nominator_id = int(nominator_id)
        nominee_id = int(nominee_id)
    except (TypeError, ValueError):
        return jsonify({"error": "无效的提名参数"}), 400

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    player, auth_error = _require_player_auth(game, nominator_id, data)
    if auth_error:
        return auth_error
    if game.current_phase != "day":
        return jsonify({"error": "当前不是白天，不能提名"}), 400

    has_active_vote = any(n.get("status") == "voting" for n in game.nominations)
    if has_active_vote:
        return jsonify({"error": "当前已有正在投票的提名"}), 400

    result = game.nominate(nominator_id, nominee_id)
    if not result.get("success"):
        return jsonify({"error": result.get("error", "提名失败")}), 400

    nomination = result.get("nomination", {})
    if result.get("virgin_triggered", False):
        game_end = game.check_game_end(apply_scarlet_woman=True, allow_mayor_day_end=True)
        started_night = False
        if not game_end.get("ended"):
            game.start_night()
            _start_auto_night_loop(game)
            started_night = True

        return jsonify({
            "success": True,
            "nomination": _serialize_nomination(nomination),
            "virgin_triggered": True,
            "executed_player": result.get("executed_player"),
            "execution_result": {"success": True, "executed": True, "reason": "virgin_ability"},
            "started_night": started_night,
            "night_number": game.night_number,
            "game_end": game_end
        })

    if nomination and nomination.get("status") == "pending":
        nomination["status"] = "voting"
        nomination["vote_started_at"] = time.time()
        nomination["vote_deadline_at"] = time.time() + getattr(game, "vote_window_seconds", AUTO_DAY_VOTE_SECONDS)
        _start_vote_timeout_loop(game, nomination["id"])

    return jsonify({
        "success": True,
        "nomination": _serialize_nomination(nomination),
        "virgin_triggered": result.get("virgin_triggered", False),
        "executed_player": result.get("executed_player")
    })

@player_bp.route('/api/player/vote', methods=['POST'])
def player_vote():
    """玩家投票"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    nomination_id = data.get('nomination_id')
    vote_value = data.get('vote')
    try:
        player_id = int(player_id)
        nomination_id = int(nomination_id)
    except (TypeError, ValueError):
        return jsonify({"error": "无效的投票参数"}), 400
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error

    nomination = next((n for n in game.nominations if n["id"] == nomination_id), None)
    if not nomination:
        return jsonify({"error": "无效的提名"}), 400

    if nomination.get("status") != "voting":
        return jsonify({"error": "当前提名未处于投票阶段"}), 400

    result = game.vote(nomination_id, player_id, vote_value)
    if not result.get("success"):
        return jsonify({"error": result.get("error", "投票失败")}), 400

    updated_nom = next((n for n in game.nominations if n["id"] == nomination_id), nomination)
    voted_ids = {v.get("voter_id") for v in updated_nom.get("votes", [])}
    eligible_voter_ids = set(_get_eligible_voter_ids(game))
    if updated_nom.get("status") == "voting" and eligible_voter_ids.issubset(voted_ids):
        _close_nomination_if_active(game, updated_nom, "all_voted")
        updated_nom = next((n for n in game.nominations if n["id"] == nomination_id), updated_nom)

    return jsonify({
        "success": True,
        "vote_count": updated_nom.get("vote_count", 0),
        "total_voters": len(updated_nom.get("votes", [])),
        "nomination": _serialize_nomination(updated_nom)
    })

@player_bp.route('/api/player/execute_active_nomination', methods=['POST'])
def execute_active_nomination():
    """房主强制结束当前投票并执行处决"""
    data = request.json
    game_id = data.get('game_id')
    owner_token = data.get('owner_token')

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    if not _is_owner(game, owner_token):
        return jsonify({"error": "仅房主可结算投票"}), 403

    nomination = _get_active_voting_nomination(game)
    if not nomination:
        return jsonify({"error": "当前没有进行中的投票"}), 400

    result = _close_nomination_if_active(game, nomination, "owner_forced")
    if not result.get("success"):
        return jsonify({"error": result.get("error", "结算失败")}), 400

    execution_result = None
    game_end = None

    if result.get("on_the_block"):
        leading_id = game.day_leading_nomination_id
        if leading_id:
            execution_result = game.execute(leading_id)
            if execution_result.get("success") and "game_end" not in execution_result:
                execution_result["game_end"] = game.check_game_end(apply_scarlet_woman=True)
            if execution_result.get("game_end"):
                game_end = execution_result["game_end"]

    return jsonify({
        "success": True,
        "result": result,
        "execution_result": execution_result,
        "game_end": game_end
    })

@player_bp.route('/api/player/end_day', methods=['POST'])
def end_day_by_owner():
    """房主结束白天并进入夜晚"""
    data = request.json
    game_id = data.get('game_id')
    owner_token = data.get('owner_token')

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    if not _is_owner(game, owner_token):
        return jsonify({"error": "仅房主可结束白天"}), 403
    if game.current_phase != "day":
        game_end = game.check_game_end(apply_scarlet_woman=True, allow_mayor_day_end=True)
        return jsonify({
            "success": True,
            "already_transitioned": True,
            "current_phase": game.current_phase,
            "game_end": game_end
        })
    active_nomination = _get_active_voting_nomination(game)
    if active_nomination:
        deadline = active_nomination.get("vote_deadline_at", 0)
        if deadline and time.time() >= float(deadline):
            close_result = _close_nomination_if_active(game, active_nomination, "owner_deadline_finalize")
            if not close_result.get("success"):
                return jsonify({"error": close_result.get("error", "结算失败")}), 400
        else:
            return jsonify({"error": "仍有提名在投票中，请先结束当前投票"}), 400

    result = _end_day_and_start_night(game)
    if not result.get("success"):
        return jsonify({"error": result.get("error", "结束白天失败")}), 400

    return jsonify(result)


@player_bp.route('/api/player/use_ability', methods=['POST'])
def player_use_ability():
    """玩家通用技能入口"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    target_id = data.get('target_id')
    targets = data.get('targets', [])

    try:
        player_id = int(player_id)
    except (TypeError, ValueError):
        return jsonify({"error": "无效的技能参数"}), 400

    parsed_targets = []
    if isinstance(targets, list) and len(targets) > 0:
        try:
            parsed_targets = [int(t) for t in targets]
        except (TypeError, ValueError):
            return jsonify({"error": "无效的技能参数"}), 400
    elif target_id is not None:
        try:
            parsed_targets = [int(target_id)]
        except (TypeError, ValueError):
            return jsonify({"error": "无效的技能参数"}), 400

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error

    if game.current_phase == "night":
        pending = getattr(game, 'pending_actions', {}).get(player_id)
        if not pending or pending.get("status") != "pending":
            return jsonify({"error": "未到你的夜间行动顺序"}), 400
        allowed_target_ids = {t.get("id") for t in pending.get("targets", [])}
        for tid in parsed_targets:
            if tid not in allowed_target_ids:
                return jsonify({"error": "目标不在当前可选范围"}), 400
        min_targets = pending.get("min_targets", 1)
        max_targets = pending.get("max_targets", 1)
        unique_targets = pending.get("unique_targets", True)
        ok, err, normalized_targets = _validate_targets_by_rules(parsed_targets, min_targets, max_targets, unique_targets)
        if not ok:
            return jsonify({"error": err}), 400
        _submit_pending_action_choice(game, player, pending, normalized_targets, {}, False)
        return jsonify({
            "success": True,
            "target_died": False,
            "public_message": "已按夜间顺序提交技能，等待结算",
            "queued": True
        })
    if len(parsed_targets) == 0:
        return jsonify({"error": "请至少选择一个目标"}), 400
    result = game.use_ability(player_id, parsed_targets[0])
    if not result.get("success"):
        return jsonify({"error": result.get("error", "发动失败")}), 400

    return jsonify({
        "success": True,
        "target_died": result.get("target_died", False),
        "public_message": result.get("public_message", "无事发生"),
        "game_end": result.get("game_end"),
        "queued": bool(result.get("queued", False))
    })

@player_bp.route('/api/player/public_slayer_shot', methods=['POST'])
def player_public_slayer_shot():
    return player_use_ability()


# ==================== 消息同步 API ====================

@player_bp.route('/api/player/messages/<game_id>/<int:player_id>', methods=['GET'])
def get_player_messages(game_id, player_id):
    """获取玩家的消息（来自说书人）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error
    
    messages = player.get("messages", [])
    
    return jsonify({
        "messages": messages,
        "unread_count": len([m for m in messages if not m.get("read")])
    })


@player_bp.route('/api/player/messages/<game_id>/<int:player_id>/read', methods=['POST'])
def mark_messages_read(game_id, player_id):
    """标记消息为已读"""
    data = request.json
    message_ids = data.get('message_ids', [])
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error
    
    messages = player.get("messages", [])
    for msg in messages:
        if msg.get("id") in message_ids or not message_ids:
            msg["read"] = True
    
    return jsonify({"success": True})


# ==================== 玩家连接状态 API ====================

@player_bp.route('/api/player/heartbeat', methods=['POST'])
def player_heartbeat():
    """玩家心跳，保持连接状态"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    reconnect_token = data.get('reconnect_token')
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error
    
    player["connected"] = True
    player["last_seen"] = datetime.now().isoformat()
    
    return jsonify({"success": True})


@player_bp.route('/api/player/pending_action/<game_id>/<int:player_id>', methods=['GET'])
def get_pending_action(game_id, player_id):
    """玩家获取待处理的行动请求"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error
    
    pending_actions = getattr(game, 'pending_actions', {})
    pending = pending_actions.get(player_id)
    
    if pending and pending.get("status") == "pending":
        return jsonify({
            "has_pending": True,
            "action": pending
        })
    
    if pending and pending.get("status") == "submitted":
        return jsonify({
            "has_pending": False,
            "action": pending
        })
    
    return jsonify({"has_pending": False})


@player_bp.route('/api/player/submit_action', methods=['POST'])
def submit_player_action():
    """玩家提交行动选择"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    targets = data.get('targets', [])
    extra_data = data.get('extra_data', {})
    skipped = data.get('skipped', False)
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error
    
    pending_actions = getattr(game, 'pending_actions', {})
    pending = pending_actions.get(player_id)
    
    if not pending or pending.get("status") != "pending":
        return jsonify({"error": "没有待处理的行动"}), 400
    min_targets = pending.get("min_targets", 1)
    max_targets = pending.get("max_targets", 1)
    unique_targets = pending.get("unique_targets", True)
    ok, err, normalized_targets = _validate_targets_by_rules(targets, min_targets, max_targets, unique_targets)
    if not skipped and not ok:
        return jsonify({"error": err}), 400
    if skipped:
        normalized_targets = []
    else:
        role_id = pending.get("role_id") or (player.get("role") or {}).get("id", "")
        action_type = pending.get("action_type")
        ok, err = _validate_night_action_constraints(game, player, role_id, action_type, normalized_targets)
        if not ok:
            return jsonify({"error": err}), 400
    _submit_pending_action_choice(game, player, pending, normalized_targets, extra_data, skipped)

    if pending.get("action_type") == "day_action":
        result = game.resolve_day_action(player_id, normalized_targets, skipped)
        if not result.get("success"):
            pending["status"] = "pending"
            pending["choice"] = None
            return jsonify({"error": result.get("error", "白天行动处理失败")}), 400
        return jsonify({
            "success": True,
            "message": result.get("message", "选择已提交"),
            "day_action_result": result
        })
    
    return jsonify({
        "success": True,
        "message": "选择已提交"
    })


# ==================== 守鸦人玩家端 API ====================

@player_bp.route('/api/player/ravenkeeper_status/<game_id>/<int:player_id>', methods=['GET'])
def get_ravenkeeper_status(game_id, player_id):
    """检查守鸦人玩家是否在本夜被触发（死亡后需要选择查验目标）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error

    # 检查是否是守鸦人且已被触发
    is_ravenkeeper = (player.get("role") and player["role"].get("id") == "ravenkeeper")
    triggered = player.get("ravenkeeper_triggered", False)
    already_chosen = player.get("ravenkeeper_choice_made", False)

    if is_ravenkeeper and triggered and not already_chosen:
        all_players = [
            {"id": p["id"], "name": p["name"]}
            for p in game.players if p["id"] != player_id
        ]
        return jsonify({
            "triggered": True,
            "already_chosen": False,
            "targets": all_players,
            "description": "你在夜间死亡！请选择一名玩家，你将得知他的角色。"
        })

    if is_ravenkeeper and triggered and already_chosen:
        return jsonify({
            "triggered": True,
            "already_chosen": True,
            "result": player.get("ravenkeeper_result")
        })

    return jsonify({"triggered": False})


@player_bp.route('/api/player/ravenkeeper_choose', methods=['POST'])
def ravenkeeper_choose():
    """守鸦人玩家提交查验选择，立即返回目标角色"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    target_id = data.get('target_id')

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error
    target = next((p for p in game.players if p["id"] == target_id), None)

    if not target:
        return jsonify({"error": "无效的玩家"}), 400

    if not player.get("ravenkeeper_triggered"):
        return jsonify({"error": "守鸦人未被触发"}), 400

    if player.get("ravenkeeper_choice_made"):
        return jsonify({"error": "已经做出选择", "result": player.get("ravenkeeper_result")}), 400

    info = game.generate_info(player_id, "ravenkeeper", [target_id])
    if not isinstance(info, dict):
        return jsonify({"error": "守鸦人信息生成失败"}), 500
    role_name = info.get("target_role")
    if not role_name:
        message = str(info.get("message", ""))
        role_name = message.split(" 的角色是 ")[-1] if " 的角色是 " in message else "未知"

    result_data = {
        "target_id": target_id,
        "target_name": target["name"],
        "role_name": role_name,
        "message": f"{target['name']} 的角色是 {role_name}"
    }

    # 记录选择
    player["ravenkeeper_choice_made"] = True
    player["ravenkeeper_result"] = result_data

    game.add_log(
        f"[守鸦人] {player['name']} 查验了 {target['name']}，得知角色为 {role_name}",
        "night"
    )

    # 发送夜间信息给玩家
    _push_player_message(
        game,
        player_id,
        "night_result",
        "🌙 守鸦人查验结果",
        f"{target['name']} 的角色是 {role_name}",
        result_data
    )

    # 标记为已提交，让自动夜间循环继续
    if hasattr(game, 'pending_actions') and player_id in game.pending_actions:
        game.pending_actions[player_id]["status"] = "submitted"
        # 构造一个符合 choice 结构的字典
        game.pending_actions[player_id]["choice"] = {
            "targets": [target_id],
            "target_names": [target["name"]],
            "extra_data": {},
            "skipped": False,
            "submitted_at": datetime.now().isoformat()
        }
    
    # 同时也记录到 player_night_choices
    if not hasattr(game, 'player_night_choices'):
        game.player_night_choices = {}
    
    game.player_night_choices[player_id] = {
        "player_id": player_id,
        "player_name": player["name"],
        "role_id": "ravenkeeper",
        "role_name": "守鸦人",
        "targets": [target_id],
        "target_names": [target["name"]],
        "extra_data": {},
        "skipped": False,
        "submitted_at": datetime.now().isoformat(),
        "confirmed": False
    }

    return jsonify({
        "success": True,
        "result": result_data
    })


@player_bp.route('/api/player/day_action/<game_id>/<int:player_id>', methods=['GET'])
def get_day_action(game_id, player_id):
    """玩家获取待处理的白天行动"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error
    
    pending = _get_or_create_day_action(game, player)
    
    if pending and pending.get("status") == "pending" and pending.get("action_type") == "day_action":
        return jsonify({
            "has_pending": True,
            "action": pending
        })
    
    return jsonify({"has_pending": False})


# ==================== 麻脸巫婆特殊处理 API ====================

@player_bp.route('/api/player/pit_hag_roles/<game_id>', methods=['GET'])
def get_pit_hag_all_roles(game_id):
    """获取麻脸巫婆可选的所有角色（玩家端用，不过滤）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player_id = request.args.get("player_id")
    try:
        player_id = int(player_id)
    except (TypeError, ValueError):
        return jsonify({"error": "无效的玩家"}), 400
    _player, auth_error = _require_player_auth(game, player_id)
    if auth_error:
        return auth_error
    
    # 获取当前场上的角色
    current_role_ids = set()
    for p in game.players:
        if p.get("role"):
            current_role_ids.add(p["role"].get("id"))
    
    # 获取剧本中所有角色
    all_roles = []
    for role_type in ["townsfolk", "outsider", "minion", "demon"]:
        for role in game.script["roles"].get(role_type, []):
            all_roles.append({
                "id": role["id"],
                "name": role["name"],
                "type": role_type,
                "ability": role.get("ability", ""),
                "in_play": role["id"] in current_role_ids  # 标记是否在场
            })
    
    return jsonify({
        "roles": all_roles,
        "current_role_ids": list(current_role_ids)
    })


@player_bp.route('/api/player/submit_pit_hag_action', methods=['POST'])
def submit_pit_hag_action():
    """玩家提交麻脸巫婆的行动"""
    data = request.json
    game_id = data.get('game_id')
    player_id = data.get('player_id')
    target_player_id = data.get('target_player_id')
    new_role_id = data.get('new_role_id')
    
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    player, auth_error = _require_player_auth(game, player_id, data)
    if auth_error:
        return auth_error
    target = next((p for p in game.players if p["id"] == target_player_id), None)
    
    if not target:
        return jsonify({"error": "无效的玩家"}), 400
    
    # 检查角色是否在场
    current_role_ids = set()
    for p in game.players:
        if p.get("role"):
            current_role_ids.add(p["role"].get("id"))
    
    role_in_play = new_role_id in current_role_ids
    
    # 获取角色信息
    new_role = None
    new_role_type = None
    for role_type in ["townsfolk", "outsider", "minion", "demon"]:
        for role in game.script["roles"].get(role_type, []):
            if role["id"] == new_role_id:
                new_role = role
                new_role_type = role_type
                break
        if new_role:
            break
    
    # 存储选择
    if not hasattr(game, 'player_night_choices'):
        game.player_night_choices = {}
    
    game.player_night_choices[player_id] = {
        "player_id": player_id,
        "player_name": player["name"],
        "role_id": "pit_hag",
        "role_name": "麻脸巫婆",
        "targets": [target_player_id],
        "target_names": [target["name"]],
        "extra_data": {
            "new_role_id": new_role_id,
            "new_role_name": new_role["name"] if new_role else "未知",
            "new_role_type": new_role_type,
            "role_in_play": role_in_play,
            "is_demon": new_role_type == "demon",
            "target_old_role": target.get("role", {}).get("name", "未知")
        },
        "submitted_at": datetime.now().isoformat(),
        "confirmed": False
    }
    
    # 更新pending_actions状态
    if hasattr(game, 'pending_actions') and player_id in game.pending_actions:
        game.pending_actions[player_id]["status"] = "submitted"
        game.pending_actions[player_id]["choice"] = game.player_night_choices[player_id]
    
    if role_in_play:
        game.add_log(f"[玩家选择] 麻脸巫婆选择将 {target['name']} 变为 {new_role['name']}（角色在场，无事发生）", "player_action")
    else:
        game.add_log(f"[玩家选择] 麻脸巫婆选择将 {target['name']} 变为 {new_role['name']}", "player_action")
    
    return jsonify({
        "success": True,
        "role_in_play": role_in_play,
        "is_demon": new_role_type == "demon",
        "message": "角色已在场，无事发生" if role_in_play else "选择已提交，等待说书人处理"
    })


# ==================== 服务器连接接口 API ====================

_server_config = {
    "mode": "local",
    "remote_url": None,
    "api_key": None,
    "sync_enabled": False,
    "websocket_url": None
}


@player_bp.route('/api/server/config', methods=['GET'])
def get_server_config():
    """获取服务器连接配置"""
    return jsonify({
        "mode": _server_config["mode"],
        "remote_url": _server_config["remote_url"],
        "sync_enabled": _server_config["sync_enabled"],
        "websocket_url": _server_config["websocket_url"],
        "supported_modes": ["local", "remote", "hybrid"],
        "description": {
            "local": "本地模式 - 所有玩家连接到同一局域网",
            "remote": "远程模式 - 通过远程服务器中转",
            "hybrid": "混合模式 - 本地优先，远程备份"
        }
    })


@player_bp.route('/api/server/config', methods=['POST'])
def update_server_config():
    """更新服务器连接配置"""
    global _server_config
    data = request.json

    if 'mode' in data:
        _server_config["mode"] = data["mode"]
    if 'remote_url' in data:
        _server_config["remote_url"] = data["remote_url"]
    if 'api_key' in data:
        _server_config["api_key"] = data["api_key"]
    if 'sync_enabled' in data:
        _server_config["sync_enabled"] = data["sync_enabled"]
    if 'websocket_url' in data:
        _server_config["websocket_url"] = data["websocket_url"]

    return jsonify({"success": True, "config": _server_config})


@player_bp.route('/api/server/sync_state', methods=['POST'])
def sync_game_state_to_server():
    """将游戏状态同步到远程服务器（未来用）"""
    if _server_config["mode"] == "local":
        return jsonify({"success": False, "message": "当前为本地模式，无需同步"})

    data = request.json
    game_id = data.get('game_id')

    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    state = game.to_dict()

    remote_url = _server_config.get("remote_url")
    if not remote_url:
        return jsonify({
            "success": False,
            "message": "远程服务器URL未配置",
            "state_snapshot": state
        })

    return jsonify({
        "success": True,
        "message": "状态已准备同步（需实现远程推送）",
        "remote_url": remote_url,
        "state_snapshot": state
    })


@player_bp.route('/api/server/pull_state', methods=['POST'])
def pull_game_state_from_server():
    """从远程服务器拉取游戏状态（未来用）"""
    if _server_config["mode"] == "local":
        return jsonify({"success": False, "message": "当前为本地模式"})

    remote_url = _server_config.get("remote_url")
    if not remote_url:
        return jsonify({"success": False, "message": "远程服务器URL未配置"})

    return jsonify({
        "success": True,
        "message": "远程拉取接口已就绪（需实现远程请求）",
        "remote_url": remote_url
    })


@player_bp.route('/api/server/health', methods=['GET'])
def server_health():
    """服务器健康检查"""
    active_games = len(games) if games else 0
    total_players = 0
    online_players = 0

    if games:
        for game in games.values():
            total_players += len(game.players)
            for p in game.players:
                if p.get("connected"):
                    online_players += 1

    return jsonify({
        "status": "healthy",
        "mode": _server_config["mode"],
        "active_games": active_games,
        "total_players": total_players,
        "online_players": online_players,
        "version": "1.0.0"
    })
