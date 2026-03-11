from flask import Blueprint, jsonify, request

gameplay_bp = Blueprint("gameplay_routes", __name__)
_games = {}


def init_gameplay_routes(games_store):
    global _games
    _games = games_store


@gameplay_bp.route('/api/game/<game_id>/check_ravenkeeper', methods=['GET'])
def check_ravenkeeper(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    game = _games[game_id]
    result = game.check_ravenkeeper_trigger()
    return jsonify(result)


@gameplay_bp.route('/api/game/<game_id>/generate_info', methods=['POST'])
def generate_info(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    data = request.json
    game = _games[game_id]
    targets = data.get('targets', [])
    info = game.generate_info(
        data.get('player_id'),
        data.get('info_type'),
        targets=targets
    )
    return jsonify(info if info else {"message": "无法生成信息"})


@gameplay_bp.route('/api/game/<game_id>/kill_player', methods=['POST'])
def kill_player(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    data = request.json
    game = _games[game_id]
    player_id = data.get('player_id')
    cause = data.get('cause', '说书人判定')
    player = next((p for p in game.players if p["id"] == player_id), None)
    if player:
        player["alive"] = False
        game.add_log(f"{player['name']} 死亡 ({cause})", "death")
        return jsonify({
            "success": True,
            "game_end": game.check_game_end(apply_scarlet_woman=True)
        })
    return jsonify({"success": False, "error": "无效的玩家"})


@gameplay_bp.route('/api/game/<game_id>/revive_player', methods=['POST'])
def revive_player(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    data = request.json
    game = _games[game_id]
    player_id = data.get('player_id')
    player = next((p for p in game.players if p["id"] == player_id), None)
    if player:
        player["alive"] = True
        player["vote_token"] = True
        game.add_log(f"{player['name']} 复活了", "revive")
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "无效的玩家"})


@gameplay_bp.route('/api/game/<game_id>/slayer_ability', methods=['POST'])
def slayer_ability(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    data = request.json
    game = _games[game_id]
    slayer_id = data.get('slayer_id')
    target_id = data.get('target_id')
    try:
        slayer_id = int(slayer_id)
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"error": "无效的参数"}), 400
    result = game.declare_slayer_shot(slayer_id, target_id)
    if not result.get("success"):
        return jsonify({"error": result.get("error", "发动失败")}), 400
    result["slayer_name"] = result.get("shooter_name")
    result["ability_used"] = bool(next((p for p in game.players if p["id"] == slayer_id), {}).get("ability_used", False))
    if not result.get("target_died"):
        result["reason"] = "无事发生"
    return jsonify(result)


@gameplay_bp.route('/api/game/<game_id>/slayer_status', methods=['GET'])
def get_slayer_status(game_id):
    if game_id not in _games:
        return jsonify({"error": "游戏不存在"}), 404
    game = _games[game_id]
    slayer = next((p for p in game.players if p.get("role") and p["role"].get("id") == "slayer" and p["alive"]), None)
    if slayer:
        return jsonify({
            "has_slayer": True,
            "slayer_id": slayer["id"],
            "slayer_name": slayer["name"],
            "ability_used": slayer.get("ability_used", False)
        })
    return jsonify({"has_slayer": False})
