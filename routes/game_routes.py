from datetime import datetime
import secrets
from flask import Blueprint, jsonify, request, redirect


game_routes_bp = Blueprint('game_routes', __name__)
games = {}
SCRIPTS = {}
Game = None
get_role_distribution = None
get_night_action_type = None


def init_game_routes(games_ref, scripts_ref, game_cls, get_role_distribution_fn, get_night_action_type_fn):
    global games, SCRIPTS, Game, get_role_distribution, get_night_action_type
    games = games_ref
    SCRIPTS = scripts_ref
    Game = game_cls
    get_role_distribution = get_role_distribution_fn
    get_night_action_type = get_night_action_type_fn


@game_routes_bp.route('/')
def index():
    return redirect('/player')


@game_routes_bp.route('/api/scripts', methods=['GET'])
def get_scripts():
    scripts_list = []
    for script_id, script in SCRIPTS.items():
        scripts_list.append({
            "id": script_id,
            "name": script["name"],
            "name_en": script["name_en"],
            "description": script["description"]
        })
    return jsonify(scripts_list)


@game_routes_bp.route('/api/script/<script_id>', methods=['GET'])
def get_script_detail(script_id):
    if script_id not in SCRIPTS:
        return jsonify({"error": "剧本不存在"}), 404
    return jsonify(SCRIPTS[script_id])


@game_routes_bp.route('/api/role_distribution/<int:player_count>', methods=['GET'])
def get_distribution(player_count):
    distribution = get_role_distribution(player_count)
    return jsonify(distribution)


@game_routes_bp.route('/api/game/create', methods=['POST'])
def create_game():
    data = request.json
    script_id = data.get('script_id')
    player_count = data.get('player_count')

    if script_id not in SCRIPTS:
        return jsonify({"error": "无效的剧本"}), 400

    if script_id == 'sects_and_violets':
        return jsonify({"error": "梦殒春宵仍在建设中，当前版本暂不开放创建"}), 400

    if not 5 <= player_count <= 16:
        return jsonify({"error": "玩家数量必须在5-16之间"}), 400

    game_id = f"game_{len(games) + 1}_{int(datetime.now().timestamp())}"
    game = Game(game_id, script_id, player_count)
    owner_token = secrets.token_urlsafe(24)
    game.owner_token = owner_token
    if len(games) >= 10:
        oldest_game_id = next(iter(games))
        del games[oldest_game_id]

    games[game_id] = game

    return jsonify({
        "success": True,
        "game_id": game_id,
        "owner_token": owner_token,
        "game": game.to_dict()
    })


@game_routes_bp.route('/api/game/<game_id>', methods=['GET'])
def get_game(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    return jsonify(games[game_id].to_dict())


@game_routes_bp.route('/api/game/<game_id>/roles', methods=['GET'])
def get_game_roles(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    return jsonify(games[game_id].get_available_roles())


@game_routes_bp.route('/api/game/<game_id>/assign_random', methods=['POST'])
def assign_random_roles(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    player_names = data.get('player_names', [])
    hide_roles = bool(data.get('hide_roles', False))

    game = games[game_id]
    if len(player_names) != game.player_count:
        return jsonify({"error": f"需要 {game.player_count} 名玩家"}), 400

    players = game.assign_roles_randomly(player_names)
    response_players = players
    if hide_roles:
        response_players = [{
            "id": p["id"],
            "name": p["name"],
            "connected": p.get("connected", False),
            "alive": p.get("alive", True)
        } for p in players]
    return jsonify({
        "success": True,
        "players": response_players
    })


@game_routes_bp.route('/api/game/<game_id>/assign_manual', methods=['POST'])
def assign_manual_roles(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    assignments = data.get('assignments', [])

    game = games[game_id]
    if len(assignments) != game.player_count:
        return jsonify({"error": f"需要 {game.player_count} 名玩家"}), 400

    players = game.assign_roles_manually(assignments)
    return jsonify({
        "success": True,
        "players": players
    })


@game_routes_bp.route('/api/game/<game_id>/start_night', methods=['POST'])
def start_night(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    if getattr(game, "mastermind_pending", False):
        resolution_day = getattr(game, "mastermind_resolution_day", None)
        if resolution_day is not None and game.day_number >= resolution_day and not game._had_execution_today():
            game.mastermind_pending = False
            game.mastermind_resolution_day = None
            game.mastermind_forced_winner = "good"
            game.add_log("🏆 游戏结束！善良阵营获胜！主谋延长日无人被处决", "game_end")
            return jsonify({
                "success": True,
                "game_end": {"ended": True, "winner": "good", "reason": "主谋延长日无人被处决"}
            })
    game_end_before_night = game.check_game_end(apply_scarlet_woman=True, allow_mayor_day_end=True)
    if game_end_before_night.get("ended"):
        return jsonify({
            "success": True,
            "game_end": game_end_before_night
        })

    game.start_night()
    night_order = game.get_night_order()

    return jsonify({
        "success": True,
        "night_number": game.night_number,
        "night_order": [{
            "player_id": item["player"]["id"],
            "player_name": item["player"]["name"],
            "role_id": item["role"]["id"],
            "role_name": item["role"]["name"],
            "role_type": game._get_role_type(item["role"]),
            "ability": item["role"]["ability"],
            "order": item["order"],
            "action_type": get_night_action_type(item["role"]["id"], game._get_role_type(item["role"]))
        } for item in night_order],
        "alive_players": [{"id": p["id"], "name": p["name"]} for p in game.players if p["alive"]]
    })


@game_routes_bp.route('/api/game/<game_id>/night_action', methods=['POST'])
def record_night_action(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    game.record_night_action(
        data.get('player_id'),
        data.get('action'),
        data.get('target'),
        data.get('result'),
        data.get('action_type'),
        data.get('extra_data')
    )

    return jsonify({"success": True})


@game_routes_bp.route('/api/game/<game_id>/night_death', methods=['POST'])
def add_night_death(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    game.add_night_death(data.get('player_id'), data.get('cause', '恶魔击杀'))

    return jsonify({"success": True})


@game_routes_bp.route('/api/game/<game_id>/start_day', methods=['POST'])
def start_day(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    game.start_day()
    game_end_result = game.check_game_end(apply_scarlet_woman=True)

    response = {
        "success": True,
        "day_number": game.day_number,
        "night_deaths": game.night_deaths,
        "game_end": game_end_result
    }

    if hasattr(game, 'imp_starpass') and game.imp_starpass:
        response["imp_starpass"] = game.imp_starpass

    if game_end_result.get("scarlet_woman_triggered"):
        response["scarlet_woman_triggered"] = True
        response["new_demon_name"] = game_end_result.get("new_demon")

    return jsonify(response)


@game_routes_bp.route('/api/game/<game_id>/nominate', methods=['POST'])
def nominate(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    result = game.nominate(data.get('nominator_id'), data.get('nominee_id'))

    return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/vote', methods=['POST'])
def vote(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    result = game.vote(
        data.get('nomination_id'),
        data.get('voter_id'),
        data.get('vote')
    )

    return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/execute', methods=['POST'])
def execute(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    result = game.execute(data.get('nomination_id'))

    if result.get("success") and "game_end" not in result:
        result["game_end"] = game.check_game_end(apply_scarlet_woman=True)

    return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/generate_info', methods=['POST'])
def generate_info(game_id):
    """Deprecated compatibility route for old tests/UI; keep logic in the main game/player flow."""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    player_id = data.get('player_id')
    info_type = data.get('info_type')
    targets = data.get('targets')
    info = game.generate_info(player_id, info_type, targets)

    if info is None:
        return jsonify({"error": "无法生成信息"}), 400
    if isinstance(info, dict) and info_type:
        info.setdefault("info_type", info_type)
    return jsonify(info)


@game_routes_bp.route('/api/game/<game_id>/slayer_ability', methods=['POST'])
def slayer_ability(game_id):
    """Deprecated compatibility route for old tests/UI; keep logic in the main game/player flow."""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    result = game.declare_slayer_shot(data.get('slayer_id'), data.get('target_id'))

    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@game_routes_bp.route('/api/game/<game_id>/player_status', methods=['POST'])
def update_player_status(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    result = game.update_player_status(
        data.get('player_id'),
        data.get('status_type'),
        data.get('value')
    )

    return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/status', methods=['GET'])
def get_game_status(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    return jsonify({
        "phase": game.current_phase,
        "day_number": game.day_number,
        "night_number": game.night_number,
        "demon_kills": getattr(game, 'demon_kills', []),
        "protected_players": getattr(game, 'protected_players', []),
        "night_deaths": getattr(game, 'night_deaths', [])
    })


@game_routes_bp.route('/api/game/<game_id>/set_red_herring', methods=['POST'])
def set_red_herring(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    target_id = data.get('target_id')

    fortune_teller = next((p for p in game.players if p.get("role") and p["role"].get("id") == "fortune_teller"), None)
    if not fortune_teller:
        return jsonify({"error": "场上没有占卜师"}), 400

    target = next((p for p in game.players if p["id"] == target_id), None)
    if not target:
        return jsonify({"error": "无效的目标玩家"}), 400

    if target["role_type"] not in ["townsfolk", "outsider"]:
        return jsonify({"error": "红鲱鱼必须是善良玩家"}), 400

    fortune_teller["red_herring_id"] = target_id
    game.add_log(f"占卜师的红鲱鱼已设置为 {target['name']}", "setup")

    return jsonify({"success": True, "red_herring": target["name"]})


@game_routes_bp.route('/api/game/<game_id>/mayor_substitute', methods=['POST'])
def mayor_substitute(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    substitute_id = data.get('substitute_id')

    mayor = next((p for p in game.players if p.get("role") and p["role"].get("id") == "mayor"), None)
    if not mayor:
        return jsonify({"error": "场上没有镇长"}), 400

    death_lists = [game.night_deaths]
    if getattr(game, "_pre_process_results", None):
        death_lists.append(game._pre_process_results)
    if not any(any(d.get("mayor_targeted") and d.get("player_id") == mayor["id"] for d in deaths) for deaths in death_lists):
        if game.current_phase == "night" and getattr(game, "demon_kills", None):
            if not getattr(game, "_night_kills_processed", False):
                game._pre_process_results = game.process_night_kills()
                game._night_kills_processed = True
            if getattr(game, "_pre_process_results", None):
                death_lists.append(game._pre_process_results)

    mayor_target_death = None
    for deaths in death_lists:
        mayor_target_death = next((d for d in deaths if d.get("mayor_targeted") and d.get("player_id") == mayor["id"]), None)
        if mayor_target_death:
            break

    if not mayor_target_death:
        return jsonify({
            "success": True,
            "auto_processed": True,
            "message": "镇长替死已并入夜间自动结算，无需手动处理",
            "substitute": None
        })

    if substitute_id:
        substitute = next((p for p in game.players if p["id"] == substitute_id), None)
        if not substitute:
            return jsonify({"error": "无效的替死玩家"}), 400

        mayor_target_death["player_id"] = substitute_id
        mayor_target_death["player_name"] = substitute["name"]
        mayor_target_death["cause"] = "镇长替死"
        mayor_target_death.pop("mayor_targeted", None)

        game.add_log(f"镇长 {mayor['name']} 的能力触发，{substitute['name']} 替镇长死亡", "night")
        return jsonify({"success": True, "substitute": substitute["name"]})
    else:
        mayor_target_death.pop("mayor_targeted", None)

        game.add_log(f"镇长 {mayor['name']} 选择不使用替死能力", "night")
        return jsonify({"success": True, "substitute": None})


@game_routes_bp.route('/api/game/<game_id>/exorcist_targets', methods=['GET'])
def get_exorcist_targets(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    previous_targets = getattr(game, 'exorcist_previous_targets', [])

    return jsonify({
        "previous_targets": previous_targets
    })


@game_routes_bp.route('/api/game/<game_id>/po_status', methods=['GET'])
def get_po_status(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    po = next((p for p in game.players if p.get("role") and p["role"].get("id") == "po" and p["alive"]), None)

    if po:
        can_kill_three = getattr(game, 'po_skipped_last_night', False)
        return jsonify({
            "has_po": True,
            "po_id": po["id"],
            "po_name": po["name"],
            "can_kill_three": can_kill_three
        })
    else:
        return jsonify({
            "has_po": False
        })


@game_routes_bp.route('/api/game/<game_id>/shabaloth_revive_targets', methods=['GET'])
def get_shabaloth_revive_targets(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    dead_players = [{"id": p["id"], "name": p["name"]} for p in game.players if not p["alive"]]

    return jsonify({
        "dead_players": dead_players
    })


@game_routes_bp.route('/api/game/<game_id>/devils_advocate_targets', methods=['GET'])
def get_devils_advocate_targets(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    previous_targets = getattr(game, 'devils_advocate_previous_targets', [])

    return jsonify({
        "previous_targets": previous_targets
    })


@game_routes_bp.route('/api/game/<game_id>/pacifist_decision', methods=['POST'])
def pacifist_decision(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]

    nomination_id = data.get('nomination_id')
    player_survives = data.get('survives', False)

    nomination = next((n for n in game.nominations if n["id"] == nomination_id), None)
    if not nomination:
        return jsonify({"error": "无效的提名"}), 400

    nominee = next((p for p in game.players if p["id"] == nomination["nominee_id"]), None)
    if not nominee:
        return jsonify({"error": "无效的被提名者"}), 400

    if player_survives:
        nomination["status"] = "pacifist_saved"
        game.add_log(f"☮️ {nominee['name']} 原本会被处决，但和平主义者的能力使其存活", "execution")
        return jsonify({
            "success": True,
            "executed": False,
            "pacifist_saved": True,
            "player": nominee
        })
    else:
        nominee["alive"] = False
        nomination["status"] = "executed"
        game.executions.append({
            "day": game.day_number,
            "executed_id": nominee["id"],
            "executed_name": nominee["name"],
            "vote_count": nomination["vote_count"]
        })
        game.add_log(f"{nominee['name']} 被处决（和平主义者未能阻止）", "execution")

        result = {"success": True, "executed": True, "player": nominee}
        if nominee.get("role_type") == "demon":
            game_end = game.check_game_end(apply_scarlet_woman=True)
            result["game_end"] = game_end

        return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/moonchild_ability', methods=['POST'])
def moonchild_ability(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    moonchild_id = data.get('moonchild_id')
    target_id = data.get('target_id')

    moonchild = next((p for p in game.players if p["id"] == moonchild_id), None)
    if not moonchild:
        return jsonify({"error": "无效的月之子玩家"}), 400

    if not moonchild.get("role") or moonchild["role"].get("id") != "moonchild":
        return jsonify({"error": "该玩家不是月之子"}), 400

    moonchild["moonchild_triggered"] = False
    game.pending_moonchild = None

    if not target_id:
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择不使用能力", "game_event")
        return jsonify({"success": True, "used": False})

    target = next((p for p in game.players if p["id"] == target_id), None)
    if not target:
        return jsonify({"error": "无效的目标玩家"}), 400

    if not target["alive"]:
        return jsonify({"error": "目标玩家已死亡"}), 400

    target_is_good = target.get("role_type") in ["townsfolk", "outsider"]

    if target_is_good:
        target["alive"] = False
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择了 {target['name']}（善良玩家），{target['name']} 死亡！", "death")
        game_end = game.check_game_end(apply_scarlet_woman=True)

        return jsonify({
            "success": True,
            "used": True,
            "target_died": True,
            "target_name": target["name"],
            "game_end": game_end
        })
    else:
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择了 {target['name']}（邪恶玩家），目标存活", "game_event")
        return jsonify({
            "success": True,
            "used": True,
            "target_died": False,
            "target_name": target["name"]
        })


@game_routes_bp.route('/api/game/<game_id>/check_moonchild', methods=['GET'])
def check_moonchild(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]

    pending_id = getattr(game, 'pending_moonchild', None)
    if pending_id:
        moonchild = next((p for p in game.players if p["id"] == pending_id), None)
        if moonchild and moonchild.get("moonchild_triggered"):
            alive_players = [{"id": p["id"], "name": p["name"]} for p in game.players if p["alive"]]
            return jsonify({
                "has_moonchild": True,
                "moonchild_id": pending_id,
                "moonchild_name": moonchild["name"],
                "alive_players": alive_players
            })

    return jsonify({"has_moonchild": False})


@game_routes_bp.route('/api/game/<game_id>/goon_effect', methods=['POST'])
def goon_effect(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    data = request.json
    game = games[game_id]
    selector_id = data.get('selector_id')
    goon_id = data.get('goon_id')

    goon = next((p for p in game.players if p["id"] == goon_id), None)
    if not goon or goon.get("role", {}).get("id") != "goon":
        return jsonify({"error": "无效的莽夫玩家"}), 400

    selector = next((p for p in game.players if p["id"] == selector_id), None)
    if not selector:
        return jsonify({"error": "无效的选择者"}), 400

    if getattr(game, 'goon_chosen_tonight', False):
        return jsonify({
            "success": True,
            "already_chosen": True,
            "message": "莽夫今晚已被其他玩家选择"
        })

    game.goon_chosen_tonight = True
    goon_affected = goon.get("drunk") or goon.get("poisoned")

    result = {
        "success": True,
        "goon_name": goon["name"],
        "selector_name": selector["name"],
        "already_chosen": False
    }

    if not goon_affected:
        selector["drunk"] = True
        selector["drunk_until"] = {
            "day": game.day_number + 1,
            "night": game.night_number + 1
        }

        selector_alignment = selector.get("role_type")
        if selector_alignment in ["townsfolk", "outsider"]:
            goon["goon_alignment"] = "good"
            result["new_alignment"] = "善良"
        else:
            goon["goon_alignment"] = "evil"
            result["new_alignment"] = "邪恶"

        game.add_log(f"💪 {selector['name']} 选择了莽夫 {goon['name']}，{selector['name']} 喝醉了，莽夫变为{result['new_alignment']}阵营", "night")
        result["selector_drunk"] = True
        result["alignment_changed"] = True
    else:
        game.add_log(f"💪 {selector['name']} 选择了莽夫 {goon['name']}（莽夫醉酒/中毒，能力无效）", "night")
        result["selector_drunk"] = False
        result["alignment_changed"] = False

    return jsonify(result)


@game_routes_bp.route('/api/game/<game_id>/pit_hag_roles', methods=['GET'])
def get_pit_hag_roles(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    game = games[game_id]
    current_role_ids = set()
    for p in game.players:
        if p.get("role"):
            current_role_ids.add(p["role"].get("id"))

    available_roles = []
    for role_type in ["townsfolk", "outsider", "minion", "demon"]:
        for role in game.script["roles"].get(role_type, []):
            if role["id"] not in current_role_ids:
                available_roles.append({
                    "id": role["id"],
                    "name": role["name"],
                    "type": role_type,
                    "ability": role.get("ability", "")
                })

    return jsonify({
        "available_roles": available_roles,
        "current_roles": list(current_role_ids)
    })


@game_routes_bp.route('/api/game/<game_id>/code', methods=['GET'])
def get_game_code(game_id):
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404

    parts = game_id.split('_')
    if len(parts) >= 3:
        short_code = parts[-1][-6:]
    else:
        short_code = game_id[-8:]

    return jsonify({
        "game_id": game_id,
        "short_code": short_code,
        "full_code": game_id
    })
