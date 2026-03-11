def generate_chef_info(game, player, is_drunk_or_poisoned=False):
    evil_players = []
    for p in game.players:
        if (p.get("role") or {}).get("id") == "recluse" and game._is_recluse_between_demon_and_evil_traveler(p):
            game.add_log(f"[系统提示] 为避免暴露旅行者阵营，厨师信息中不将陌客 {p['name']} 计为邪恶", "info")
            continue
        if game._registers_as_evil_for_good_info(p, "厨师"):
            evil_players.append(p)
    pairs = 0
    for i, p in enumerate(game.players):
        if p in evil_players:
            next_idx = (i + 1) % len(game.players)
            if game.players[next_idx] in evil_players:
                pairs += 1
    return {
        "info_type": "chef",
        "pairs": pairs,
        "message": f"有 {pairs} 对邪恶玩家相邻",
        "is_drunk_or_poisoned": is_drunk_or_poisoned
    }


def generate_empath_info(game, player, is_drunk_or_poisoned=False, recluse_empath_rate=0.5, random_module=None):
    if random_module is None:
        import random as random_module
    player_idx = next((i for i, p in enumerate(game.players) if p["id"] == player["id"]), -1)
    if player_idx == -1:
        return {"message": "无法确定位置", "is_drunk_or_poisoned": is_drunk_or_poisoned}
    evil_neighbors = 0
    for offset in range(1, len(game.players)):
        idx = (player_idx - offset) % len(game.players)
        neighbor = game.players[idx]
        if neighbor["alive"]:
            if neighbor.get("role") and neighbor["role"].get("id") == "recluse":
                if game._is_ability_active(neighbor, "被动技能:recluse_empath") and random_module.random() < recluse_empath_rate:
                    evil_neighbors += 1
                    game.add_log(f"[系统提示] 陌客 {neighbor['name']} 被共情者误认为邪恶", "info")
            elif game._registers_as_evil_for_good_info(neighbor, "共情者"):
                evil_neighbors += 1
            break
    for offset in range(1, len(game.players)):
        idx = (player_idx + offset) % len(game.players)
        neighbor = game.players[idx]
        if neighbor["alive"]:
            if neighbor.get("role") and neighbor["role"].get("id") == "recluse":
                if game._is_ability_active(neighbor, "被动技能:recluse_empath") and random_module.random() < recluse_empath_rate:
                    evil_neighbors += 1
                    game.add_log(f"[系统提示] 陌客 {neighbor['name']} 被共情者误认为邪恶", "info")
            elif game._registers_as_evil_for_good_info(neighbor, "共情者"):
                evil_neighbors += 1
            break
    return {
        "info_type": "empath",
        "evil_count": evil_neighbors,
        "message": f"你的存活邻居中有 {evil_neighbors} 个是邪恶的",
        "is_drunk_or_poisoned": is_drunk_or_poisoned
    }


def generate_fortune_teller_info(game, player, target_players, is_drunk_or_poisoned=False, recluse_fortune_teller_rate=0.5, random_module=None):
    if random_module is None:
        import random as random_module
    if len(target_players) < 2:
        return {
            "info_type": "fortune_teller",
            "message": "请选择两名玩家进行占卜",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    target_names = " 和 ".join([t["name"] for t in target_players])
    if is_drunk_or_poisoned:
        has_demon = random_module.choice([True, False])
        game.add_log(f"[系统] 占卜师 {player['name']} 处于醉酒/中毒状态，系统已自动生成随机结果", "info")
        return {
            "info_type": "fortune_teller",
            "has_demon": has_demon,
            "message": f"在 {target_names} 中，{'有' if has_demon else '没有'}恶魔",
            "is_drunk_or_poisoned": True
        }
    has_demon = any(t["role_type"] == "demon" for t in target_players)
    red_herring_id = player.get("red_herring_id")
    if red_herring_id and any(t["id"] == red_herring_id for t in target_players):
        has_demon = True
    for t in target_players:
        if t.get("role") and t["role"].get("id") == "recluse":
            if game._is_ability_active(t, "被动技能:recluse_fortune_teller") and random_module.random() < recluse_fortune_teller_rate:
                has_demon = True
                game.add_log(f"[系统提示] 陌客 {t['name']} 被占卜师误认为恶魔", "info")
    return {
        "info_type": "fortune_teller",
        "has_demon": has_demon,
        "message": f"在 {target_names} 中，{'有' if has_demon else '没有'}恶魔",
        "is_drunk_or_poisoned": False
    }


def generate_undertaker_info(game, player, is_drunk_or_poisoned=False):
    if not game.executions:
        return {
            "info_type": "undertaker",
            "message": "昨天没有人被处决",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    last_execution = game.executions[-1]
    executed_player = next((p for p in game.players if p["id"] == last_execution.get("executed_id")), None)
    if executed_player:
        role_name = game._registered_role_name_for_good_info(executed_player, "殡仪员")
        return {
            "info_type": "undertaker",
            "executed_role": role_name,
            "message": f"昨天被处决的玩家 {executed_player['name']} 的角色是 {role_name}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    return {
        "info_type": "undertaker",
        "message": "无法获取处决信息",
        "is_drunk_or_poisoned": is_drunk_or_poisoned
    }
