from flask import Flask, render_template, request, jsonify, session
import random
import json
import secrets
from datetime import datetime
from game_data import SCRIPTS, ROLE_TYPES, get_role_distribution, NIGHT_ORDER_PHASES, DAY_PHASES, get_night_action_type
from player_api import player_bp, init_player_api
from routes.gameplay_routes import gameplay_bp, init_gameplay_routes
from config.balance import (
    POISON_FAILURE_RATE,
    SPY_GOOD_REGISTRATION_RATE,
    MAYOR_SUBSTITUTE_RATE,
    RECLUSE_EVIL_REGISTRATION_RATE,
    RECLUSE_MINION_REGISTRATION_RATE,
    RECLUSE_UNDERTAKER_IMP_REGISTRATION_RATE,
    RECLUSE_INVESTIGATOR_MINION_REGISTRATION_RATE,
    RECLUSE_EMPATH_EVIL_REGISTRATION_RATE,
    RECLUSE_FORTUNE_TELLER_DEMON_REGISTRATION_RATE,
    RECLUSE_SLAYER_DEMON_REGISTRATION_RATE
)
from services.info_generators import (
    generate_chef_info,
    generate_empath_info,
    generate_fortune_teller_info,
    generate_undertaker_info
)

app = Flask(__name__)
app.secret_key = 'blood_on_the_clocktower_storyteller_secret_key_2026'

# 全局游戏状态存储
games = {}

# 注册玩家端蓝图
app.register_blueprint(player_bp)
init_player_api(games)
app.register_blueprint(gameplay_bp)
init_gameplay_routes(games)

class Game:
    def __init__(self, game_id, script_id, player_count):
        self.game_id = game_id
        self.script_id = script_id
        self.script = SCRIPTS[script_id]
        self.player_count = player_count
        self.players = []
        self.role_distribution = get_role_distribution(player_count)
        self.current_phase = "setup"  # setup, night, day
        self.day_number = 0
        self.night_number = 0
        self.nominations = []
        self.votes = {}
        self.executions = []
        self.night_actions = []
        self.night_deaths = []
        self.game_log = []
        self.created_at = datetime.now().isoformat()
        # 更新日期: 2026-01-05 - 驱魔人追踪
        self.exorcist_previous_targets = []  # 驱魔人上一晚目标
        self.demon_exorcised_tonight = False  # 恶魔今晚是否被驱魔
        # 更新日期: 2026-01-05 - 僵怖、沙巴洛斯、珀追踪
        self.zombuul_first_death = False  # 僵怖是否已经"假死"过
        self.po_skipped_last_night = False  # 珀上一晚是否跳过了行动
        self.shabaloth_revive_available = False  # 沙巴洛斯是否可以复活
        # 更新日期: 2026-01-09 - 恶魔代言人追踪
        self.devils_advocate_previous_targets = []  # 恶魔代言人上一晚目标
        self.devils_advocate_protected = None  # 今天被恶魔代言人保护的玩家ID
        # 更新日期: 2026-01-09 - 弄臣、月之子、莽夫追踪
        self.goon_chosen_tonight = False  # 莽夫今晚是否已被选择
        self.pending_moonchild = None  # 等待处理的月之子（死亡时触发）
        self.day_leading_nomination_id = None
        self.day_leading_vote_count = 0
        self.day_tied = False
        self.minstrel_effect_until_day = None
        self.mastermind_pending = False
        self.mastermind_resolution_day = None
        self.mastermind_forced_winner = None
        
    def to_dict(self):
        return {
            "game_id": self.game_id,
            "script_id": self.script_id,
            "script_name": self.script["name"],
            "player_count": self.player_count,
            "players": self.players,
            "role_distribution": self.role_distribution,
            "current_phase": self.current_phase,
            "day_number": self.day_number,
            "night_number": self.night_number,
            "nominations": self.nominations,
            "votes": self.votes,
            "executions": self.executions,
            "night_deaths": self.night_deaths,
            "game_log": self.game_log,
            "minstrel_effect_until_day": self.minstrel_effect_until_day,
            "mastermind_pending": self.mastermind_pending,
            "mastermind_resolution_day": self.mastermind_resolution_day
        }
    
    def add_log(self, message, log_type="info"):
        self.game_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": log_type,
            "message": message
        })
    
    def get_available_roles(self):
        """获取当前剧本的所有可用角色"""
        roles = {
            "townsfolk": self.script["roles"]["townsfolk"],
            "outsider": self.script["roles"]["outsider"],
            "minion": self.script["roles"]["minion"],
            "demon": self.script["roles"]["demon"]
        }
        return roles
    
    def assign_roles_randomly(self, player_names):
        """随机分配角色"""
        self.players = []
        available_roles = self.get_available_roles()
        distribution = self.role_distribution.copy()  # 复制一份，避免修改原始数据
        
        selected_roles = []
        
        # 首先检查是否会有设置阶段能力的角色（男爵、教父等）
        # 先预选爪牙角色
        minion_roles = available_roles["minion"].copy()
        random.shuffle(minion_roles)
        selected_minions = minion_roles[:distribution.get("minion", 0)]
        
        # 检查是否有男爵（+2外来者，-2镇民）
        has_baron = any(m["id"] == "baron" for m in selected_minions)
        # 检查是否有教父（±1外来者）
        has_godfather = any(m["id"] == "godfather" for m in selected_minions)
        
        # 计算外来者调整
        outsider_adjustment = 0
        
        # 男爵：固定 +2 外来者
        if has_baron:
            outsider_adjustment += 2
            self.add_log(f"男爵在场：外来者 +2，镇民 -2", "setup")
        
        # 教父：±1 外来者（根据当前外来者数量决定）
        if has_godfather:
            current_outsiders = distribution.get("outsider", 0) + outsider_adjustment
            if current_outsiders == 0:
                # 如果没有外来者，必须+1（否则教父无法使用能力）
                outsider_adjustment += 1
                self.add_log(f"教父在场：外来者 +1，镇民 -1（场上无外来者，必须添加）", "setup")
            else:
                # 如果有外来者，随机选择+1或-1
                godfather_choice = random.choice([1, -1])
                outsider_adjustment += godfather_choice
                if godfather_choice == 1:
                    self.add_log(f"教父在场：外来者 +1，镇民 -1", "setup")
                else:
                    self.add_log(f"教父在场：外来者 -1，镇民 +1", "setup")
        
        # 应用调整
        if outsider_adjustment != 0:
            outsider_count = distribution.get("outsider", 0) + outsider_adjustment
            townsfolk_count = distribution.get("townsfolk", 0) - outsider_adjustment
            # 确保不会出现负数
            outsider_count = max(0, outsider_count)
            townsfolk_count = max(0, townsfolk_count)
            distribution["outsider"] = outsider_count
            distribution["townsfolk"] = townsfolk_count
        
        # 选择角色（爪牙已经预选好了）
        selected_roles.extend(selected_minions)
        
        for role_type, count in distribution.items():
            if role_type == "minion":
                continue  # 爪牙已经选好了
            type_roles = available_roles[role_type].copy()
            random.shuffle(type_roles)
            selected_roles.extend(type_roles[:count])
        
        random.shuffle(selected_roles)
        
        # 为酒鬼准备假的镇民角色列表（排除已选的镇民）
        selected_townsfolk_ids = [r["id"] for r in selected_roles if self._get_role_type(r) == "townsfolk"]
        fake_townsfolk_for_drunk = [r for r in available_roles["townsfolk"] if r["id"] not in selected_townsfolk_ids]
        
        for i, name in enumerate(player_names):
            role = selected_roles[i] if i < len(selected_roles) else None
            
            # 检查是否是酒鬼，如果是则分配假的镇民角色
            is_the_drunk = role and role.get("id") == "drunk"
            displayed_role = role
            true_role = None
            
            if is_the_drunk and fake_townsfolk_for_drunk:
                # 为酒鬼随机选择一个假的镇民角色显示
                random.shuffle(fake_townsfolk_for_drunk)
                displayed_role = fake_townsfolk_for_drunk[0]
                true_role = role  # 保存真实角色（酒鬼）
            
            player = {
                "id": i + 1,
                "name": name,
                "role": displayed_role,
                "role_type": self._get_role_type(role) if role else None,  # 真实角色类型
                "true_role": true_role,  # 如果是酒鬼，存储真实角色
                "is_the_drunk": is_the_drunk,  # 是否是酒鬼
                "alive": True,
                "poisoned": False,
                "poisoned_until": None,  # 中毒结束时间 {"day": x, "night": y}
                "drunk": is_the_drunk,  # 酒鬼永久处于醉酒状态
                "drunk_until": None if not is_the_drunk else {"permanent": True},  # 酒鬼永久醉酒
                "protected": False,
                "vote_token": True,
                "ability_used": False,  # 一次性技能是否已使用
                "notes": ""
            }
            self.players.append(player)
        
        self.add_log(f"已随机分配 {len(player_names)} 名玩家的角色", "setup")
        
        # 检查是否有占卜师，如果有，需要设置红鲱鱼
        fortune_teller = next((p for p in self.players if p.get("role") and p["role"].get("id") == "fortune_teller"), None)
        if fortune_teller:
            # 随机选择一名善良玩家作为红鲱鱼
            good_players = [p for p in self.players if p["role_type"] in ["townsfolk", "outsider"] and p["id"] != fortune_teller["id"]]
            if good_players:
                red_herring = random.choice(good_players)
                fortune_teller["red_herring_id"] = red_herring["id"]
                self.add_log(f"占卜师的红鲱鱼已设置（需说书人在开局时确认或修改）", "setup")
        
        return self.players
    
    def assign_roles_manually(self, assignments):
        """手动分配角色"""
        self.players = []
        available_roles = self.get_available_roles()
        
        # 检查是否有设置阶段能力的角色
        has_baron = any(a.get("role_id") == "baron" for a in assignments)
        has_godfather = any(a.get("role_id") == "godfather" for a in assignments)
        if has_baron:
            self.add_log(f"男爵在场：请确保外来者数量比标准多2个", "setup")
        if has_godfather:
            self.add_log(f"教父在场：请确保外来者数量比标准 +1 或 -1（由说书人决定）", "setup")
        
        # 收集已分配的镇民角色ID
        assigned_townsfolk_ids = [a["role_id"] for a in assignments if a.get("role_id") and 
                                   self._get_role_type(self._find_role_by_id(a["role_id"])) == "townsfolk"]
        fake_townsfolk_for_drunk = [r for r in available_roles["townsfolk"] if r["id"] not in assigned_townsfolk_ids]
        
        for i, assignment in enumerate(assignments):
            role = self._find_role_by_id(assignment["role_id"]) if assignment.get("role_id") else None
            
            # 检查是否是酒鬼
            is_the_drunk = role and role.get("id") == "drunk"
            displayed_role = role
            true_role = None
            
            # 如果是酒鬼，检查是否指定了假角色，否则随机选择
            if is_the_drunk:
                if assignment.get("drunk_fake_role_id"):
                    displayed_role = self._find_role_by_id(assignment["drunk_fake_role_id"])
                elif fake_townsfolk_for_drunk:
                    random.shuffle(fake_townsfolk_for_drunk)
                    displayed_role = fake_townsfolk_for_drunk[0]
                true_role = role
            
            player = {
                "id": i + 1,
                "name": assignment["name"],
                "role": displayed_role,
                "role_type": self._get_role_type(role) if role else None,  # 真实角色类型
                "true_role": true_role,  # 如果是酒鬼，存储真实角色
                "is_the_drunk": is_the_drunk,  # 是否是酒鬼
                "alive": True,
                "poisoned": False,
                "poisoned_until": None,
                "drunk": is_the_drunk,  # 酒鬼永久处于醉酒状态
                "drunk_until": None if not is_the_drunk else {"permanent": True},
                "protected": False,
                "vote_token": True,
                "ability_used": False,
                "notes": ""
            }
            self.players.append(player)
        
        self.add_log(f"已手动分配 {len(assignments)} 名玩家的角色", "setup")
        
        # 更新日期: 2026-01-05 - 手动分配也需要检查并设置占卜师红鲱鱼
        # 检查是否有占卜师，如果有，需要设置红鲱鱼
        fortune_teller = next((p for p in self.players if p.get("role") and p["role"].get("id") == "fortune_teller"), None)
        if fortune_teller:
            # 随机选择一名善良玩家作为红鲱鱼
            good_players = [p for p in self.players if p["role_type"] in ["townsfolk", "outsider"] and p["id"] != fortune_teller["id"]]
            if good_players:
                red_herring = random.choice(good_players)
                fortune_teller["red_herring_id"] = red_herring["id"]
                self.add_log(f"占卜师的红鲱鱼已设置（需说书人在开局时确认或修改）", "setup")
        
        return self.players
    
    def _find_role_by_id(self, role_id):
        """根据角色ID查找角色"""
        for role_type in ["townsfolk", "outsider", "minion", "demon"]:
            for role in self.script["roles"][role_type]:
                if role["id"] == role_id:
                    return role
        return None
    
    def _get_role_type(self, role):
        """获取角色类型"""
        if not role:
            return None
        for role_type in ["townsfolk", "outsider", "minion", "demon"]:
            for r in self.script["roles"][role_type]:
                if r["id"] == role["id"]:
                    return role_type
        return None

    def _get_player_actual_role(self, player):
        if not player:
            return None
        if player.get("is_the_drunk") and player.get("true_role"):
            return player.get("true_role")
        return player.get("role")

    def _sync_player_role_type(self, player, scene=""):
        if not player:
            return None
        expected_role_type = self._get_role_type(self._get_player_actual_role(player))
        current_role_type = player.get("role_type")
        if expected_role_type != current_role_type:
            player["role_type"] = expected_role_type
            self.add_log(f"[系统] {player.get('name')} 的角色类型已纠偏：{current_role_type} -> {expected_role_type}（{scene or '自动校验'}）", "info")
        return expected_role_type

    def reconcile_player_role_types(self, scene=""):
        for player in self.players:
            self._sync_player_role_type(player, scene)
    
    def start_night(self):
        """开始夜晚"""
        self.reconcile_player_role_types("夜晚开始")
        if self.minstrel_effect_until_day is not None and self.day_number >= self.minstrel_effect_until_day:
            self.minstrel_effect_until_day = None
            self.add_log("🎻 吟游诗人的全场醉酒效果已结束", "status")
        self.night_number += 1
        self.current_phase = "night"
        self.night_deaths = []
        self.night_actions = []
        self.protected_players = []
        self.demon_kills = []
        self._night_kills_processed = False
        self._pre_process_results = None
        # 更新日期: 2026-01-05 - 重置驱魔人状态
        self.demon_exorcised_tonight = False  # 重置恶魔被驱魔状态
        # 更新日期: 2026-01-05 - 重置莽夫状态
        self.goon_chosen_tonight = False  # 重置莽夫今晚是否被选择
        
        # 重置所有玩家的保护状态和守鸦人触发状态
        for player in self.players:
            player["protected"] = False
            player.pop("ravenkeeper_triggered", None)
            player.pop("ravenkeeper_choice_made", None)
            player.pop("ravenkeeper_result", None)
            
            # 检查醉酒状态是否过期
            if player.get("drunk") and player.get("drunk_until"):
                until = player["drunk_until"]
                if until.get("permanent"):
                    pass  # 永久醉酒（酒鬼）不清除
                elif until.get("night") and self.night_number > until["night"]:
                    player["drunk"] = False
                    player["drunk_until"] = None
                    self.add_log(f"{player['name']} 的醉酒状态已结束", "status")
            
            # 检查中毒状态是否过期（投毒者的毒在入夜时结束）
            if player.get("poisoned") and player.get("poisoned_until"):
                until = player["poisoned_until"]
                if until.get("phase") == "night_start" and until.get("night") == self.night_number:
                    player["poisoned"] = False
                    player["poisoned_until"] = None
                    self.add_log(f"{player['name']} 的中毒状态已结束", "status")
            
        self.add_log(f"第 {self.night_number} 个夜晚开始", "phase")
        
    def get_night_order(self):
        """获取夜晚行动顺序"""
        night_roles = []
        is_first_night = self.night_number == 1
        
        # 定义一次性技能角色
        once_per_game_roles = [
            "slayer",       # 杀手
            "virgin",       # 贞洁者
            "courtier",     # 侍臣
            "professor",    # 教授
            "seamstress",   # 女裁缝
            "philosopher",  # 哲学家
            "artist",       # 艺术家
            "assassin"      # 刺客
        ]
        
        for player in self.players:
            if player["alive"] and player["role"]:
                role = player["role"]
                role_id = role.get("id", "")
                
                # 跳过被动触发的角色（如守鸦人、贤者等 - 只在触发时处理）
                if role.get("passive_trigger"):
                    continue
                
                # 跳过说书人控制的角色（如修补匠、造谣者等）
                if role.get("storyteller_controlled"):
                    continue
                
                # 检查是否是一次性技能且已使用
                if role_id in once_per_game_roles and player.get("ability_used", False):
                    continue  # 跳过已使用技能的一次性角色
                
                if is_first_night and role.get("first_night"):
                    night_roles.append({
                        "player": player,
                        "role": role,
                        "order": role.get("night_order", 99)
                    })
                elif not is_first_night and role.get("other_nights"):
                    night_roles.append({
                        "player": player,
                        "role": role,
                        "order": role.get("night_order", 99)
                    })
        
        # 按顺序排序
        night_roles.sort(key=lambda x: x["order"])
        return night_roles
    
    def record_night_action(self, player_id, action, target=None, result=None, action_type=None, extra_data=None):
        """记录夜间行动"""
        player = next((p for p in self.players if p["id"] == player_id), None)
        target_player = next((p for p in self.players if p["id"] == target), None) if target else None
        role = self._get_player_actual_role(player) if player else {}
        role_id = (role or {}).get("id", "")
        
        # 一次性技能角色列表
        once_per_game_roles = [
            "slayer", "virgin", "courtier", "professor", 
            "seamstress", "philosopher", "artist", "assassin"
        ]
        
        self.night_actions.append({
            "player_id": player_id,
            "action": action,
            "target": target,
            "result": result,
            "action_type": action_type,
            "time": datetime.now().strftime("%H:%M:%S")
        })

        if player and action_type in {"kill", "assassin_kill", "gossip_kill", "zombuul_kill", "shabaloth_kill", "po_kill", "poison", "protect", "pukka_poison", "drunk", "sailor_drunk", "gambler_guess", "ability_select", "exorcist", "devils_advocate", "pit_hag", "butler_master", "grandchild_select"}:
            if not self._is_ability_active(player, f"夜间行动:{action_type}"):
                self.night_actions[-1]["result"] = "能力失效"
                self.add_log(f"[夜间] {player['name']} 因醉酒/中毒导致技能失效", "night")
                return
        if target is not None and not target_player:
            self.night_actions[-1]["result"] = "非法目标"
            self.add_log(f"[夜间] {player['name'] if player else '未知玩家'} 提交了无效目标", "night")
            return
        if role_id == "monk" and action_type == "protect" and target == player_id:
            self.night_actions[-1]["result"] = "非法目标"
            self.add_log(f"[夜间] {player['name']} 不能保护自己，行动无效", "night")
            return
        if role_id == "butler" and action_type == "butler_master":
            if target == player_id:
                self.night_actions[-1]["result"] = "非法目标"
                self.add_log(f"[夜间] {player['name']} 不能选择自己作为主人，行动无效", "night")
                return
            if target_player and not target_player.get("alive", True):
                self.night_actions[-1]["result"] = "非法目标"
                self.add_log(f"[夜间] {player['name']} 选择了已死亡玩家作为主人，行动无效", "night")
                return
        
        # 处理保护类行动
        if action_type == "protect" and target:
            if not hasattr(self, 'protected_players'):
                self.protected_players = []
            self.protected_players.append(target)
            if target_player:
                target_player["protected"] = True
                self.add_log(f"[夜间] {player['name']} 保护了 {target_player['name']}", "night")
            
            # 旅店老板特殊处理：第二个目标
            if extra_data and extra_data.get("second_target"):
                second_target_id = extra_data["second_target"]
                second_target_player = next((p for p in self.players if p["id"] == second_target_id), None)
                if second_target_player:
                    self.protected_players.append(second_target_id)
                    second_target_player["protected"] = True
                    self.add_log(f"[夜间] {player['name']} 也保护了 {second_target_player['name']}", "night")
                
                # 处理其中一人醉酒
                drunk_target_id = extra_data.get("drunk_target")
                if drunk_target_id:
                    drunk_player = next((p for p in self.players if p["id"] == drunk_target_id), None)
                    if drunk_player:
                        drunk_player["drunk"] = True
                        drunk_player["drunk_until"] = {
                            "day": self.day_number + 1,
                            "night": self.night_number + 1
                        }
                        self.add_log(f"[夜间] {drunk_player['name']} 因旅店老板的能力喝醉了", "night")
        
        # 处理击杀类行动（恶魔）
        # 更新日期: 2026-01-05 - 添加驱魔人阻止恶魔行动逻辑
        elif action_type in {"kill", "assassin_kill"} and target:
            if action_type == "kill" and getattr(self, 'demon_exorcised_tonight', False):
                self.add_log(f"[夜间] {player['name']} 被驱魔人阻止，无法击杀", "night")
                if player and player.get("role", {}).get("id") == "imp" and target == player_id:
                    self.process_imp_suicide(player_id)
            else:
                if not hasattr(self, 'demon_kills'):
                    self.demon_kills = []
                self.demon_kills.append({
                    "killer_id": player_id,
                    "target_id": target,
                    "killer_name": player['name'] if player else '未知',
                    "target_name": target_player['name'] if target_player else '未知',
                    "kill_type": "assassin" if action_type == "assassin_kill" else "demon",
                    "ignore_protection": action_type == "assassin_kill"
                })
                self.add_log(f"[夜间] {player['name']} 选择击杀 {target_player['name'] if target_player else '未知'}", "night")
                self.check_and_trigger_ravenkeeper(target)
                if player and player.get("role", {}).get("id") == "imp" and target == player_id:
                    self.process_imp_suicide(player_id)

        elif action_type == "gossip_kill" and target:
            if not hasattr(self, 'demon_kills'):
                self.demon_kills = []
            self.demon_kills.append({
                "killer_id": player_id,
                "target_id": target,
                "killer_name": player['name'] if player else '未知',
                "target_name": target_player['name'] if target_player else '未知',
                "kill_type": "gossip",
                "ignore_protection": False
            })
            self.add_log(f"[夜间] {player['name']} (造谣者) 触发死亡：{target_player['name'] if target_player else '未知'}", "night")
            self.check_and_trigger_ravenkeeper(target)
        
        # 更新日期: 2026-01-05 - 僵怖击杀（如果今天没人因其能力死亡才能杀人）
        elif action_type == "zombuul_kill":
            if getattr(self, 'demon_exorcised_tonight', False):
                self.add_log(f"[夜间] {player['name']} (僵怖) 被驱魔人阻止，无法击杀", "night")
            elif target:
                # 检查今天白天是否有人死亡（被处决等）
                # 僵怖只有在"没有人因其能力死亡"时才能杀人
                # 这里简化处理：如果选择了目标就添加到击杀列表
                if not hasattr(self, 'demon_kills'):
                    self.demon_kills = []
                self.demon_kills.append({
                    "killer_id": player_id,
                    "target_id": target,
                    "killer_name": player['name'] if player else '未知',
                    "target_name": target_player['name'] if target_player else '未知',
                    "kill_type": "zombuul"
                })
                self.add_log(f"[夜间] {player['name']} (僵怖) 选择击杀 {target_player['name'] if target_player else '未知'}", "night")
                self.check_and_trigger_ravenkeeper(target)
            else:
                self.add_log(f"[夜间] {player['name']} (僵怖) 选择不击杀任何人", "night")
        
        # 更新日期: 2026-01-05 - 沙巴洛斯击杀（每晚杀两人，可选复活）
        elif action_type == "shabaloth_kill":
            if getattr(self, 'demon_exorcised_tonight', False):
                self.add_log(f"[夜间] {player['name']} (沙巴洛斯) 被驱魔人阻止，无法击杀", "night")
            else:
                if not hasattr(self, 'demon_kills'):
                    self.demon_kills = []
                
                # 第一个目标
                if target:
                    self.demon_kills.append({
                        "killer_id": player_id,
                        "target_id": target,
                        "killer_name": player['name'] if player else '未知',
                        "target_name": target_player['name'] if target_player else '未知',
                        "kill_type": "shabaloth"
                    })
                    self.add_log(f"[夜间] {player['name']} (沙巴洛斯) 选择击杀 {target_player['name']}", "night")
                    self.check_and_trigger_ravenkeeper(target)
                
                # 第二个目标（通过 extra_data 传递）
                second_target = extra_data.get("second_target") if extra_data else None
                if second_target:
                    second_target_player = next((p for p in self.players if p["id"] == second_target), None)
                    if second_target_player:
                        self.demon_kills.append({
                            "killer_id": player_id,
                            "target_id": second_target,
                            "killer_name": player['name'] if player else '未知',
                            "target_name": second_target_player['name'],
                            "kill_type": "shabaloth"
                        })
                        self.add_log(f"[夜间] {player['name']} (沙巴洛斯) 选择击杀 {second_target_player['name']}", "night")
                        self.check_and_trigger_ravenkeeper(second_target)
                
                # 复活（通过 extra_data 传递）
                revive_target = extra_data.get("revive_target") if extra_data else None
                if revive_target:
                    revive_player = next((p for p in self.players if p["id"] == revive_target), None)
                    if revive_player and not revive_player["alive"]:
                        revive_player["alive"] = True
                        revive_player["vote_token"] = True
                        self.add_log(f"[夜间] {player['name']} (沙巴洛斯) 复活了 {revive_player['name']}", "night")
        
        # 更新日期: 2026-01-05 - 珀击杀（上晚不杀则本晚可杀三人）
        elif action_type == "po_kill":
            if getattr(self, 'demon_exorcised_tonight', False):
                self.add_log(f"[夜间] {player['name']} (珀) 被驱魔人阻止，无法击杀", "night")
                # 即使被驱魔，也记录为"选择了行动"，不触发三杀
                self.po_skipped_last_night = False
            elif target is None and (extra_data is None or not extra_data.get("targets")):
                # 选择不杀任何人 - 下一晚可以杀三人
                self.po_skipped_last_night = True
                self.add_log(f"[夜间] {player['name']} (珀) 选择不击杀任何人（下一晚可杀三人）", "night")
            else:
                if not hasattr(self, 'demon_kills'):
                    self.demon_kills = []
                
                # 获取目标列表（可能是1个或3个）
                targets = extra_data.get("targets", [target]) if extra_data else [target]
                if target and target not in targets:
                    targets = [target] + targets
                
                # 清除重复并限制数量
                targets = list(dict.fromkeys([t for t in targets if t]))  # 去重且保持顺序
                can_kill_three = getattr(self, 'po_skipped_last_night', False)
                max_targets = 3 if can_kill_three else 1
                targets = targets[:max_targets]
                
                for t in targets:
                    t_player = next((p for p in self.players if p["id"] == t), None)
                    if t_player:
                        self.demon_kills.append({
                            "killer_id": player_id,
                            "target_id": t,
                            "killer_name": player['name'] if player else '未知',
                            "target_name": t_player['name'],
                            "kill_type": "po"
                        })
                        self.add_log(f"[夜间] {player['name']} (珀) 选择击杀 {t_player['name']}", "night")
                        self.check_and_trigger_ravenkeeper(t)
                
                # 重置状态
                self.po_skipped_last_night = False
        
        # 处理投毒类行动
        elif action_type == "poison" and target:
            if target_player:
                target_player["poisoned"] = True
                # 投毒持续到第二天夜晚开始时（当晚和明天白天有效，再次入夜时结束）
                target_player["poisoned_until"] = {"night": self.night_number + 1, "phase": "night_start"}
                self.add_log(f"[夜间] {player['name']} 对 {target_player['name']} 下毒（持续到明晚入夜）", "night")
        
        # 处理普卡的特殊投毒（选择新目标中毒，前一晚目标死亡）
        elif action_type == "pukka_poison" and target:
            if target_player and player:
                # 获取普卡之前的中毒目标
                previous_victim_id = player.get("pukka_previous_target")
                
                # 前一晚的目标死亡（如果存在且未被保护）
                if previous_victim_id:
                    previous_victim = next((p for p in self.players if p["id"] == previous_victim_id), None)
                    if previous_victim and previous_victim["alive"]:
                        # 检查是否被保护
                        is_protected = previous_victim.get("protected", False)
                        if not is_protected:
                            # 添加到恶魔击杀列表
                            if not hasattr(self, 'demon_kills'):
                                self.demon_kills = []
                            self.demon_kills.append({
                                "killer_id": player_id,
                                "target_id": previous_victim_id,
                                "killer_name": player['name'],
                                "target_name": previous_victim['name'],
                                "kill_type": "pukka_delayed"
                            })
                            self.add_log(f"[夜间] {previous_victim['name']} 因普卡的毒素死亡", "night")
                            self.check_and_trigger_ravenkeeper(previous_victim_id)
                        else:
                            self.add_log(f"[夜间] {previous_victim['name']} 被保护，免疫普卡的毒杀", "night")
                        
                        # 清除前一个目标的中毒状态（恢复健康）
                        previous_victim["poisoned"] = False
                        previous_victim.pop("poisoned_until", None)
                
                # 新目标中毒
                target_player["poisoned"] = True
                target_player["poisoned_by_pukka"] = True
                # 普卡的毒持续到被新目标取代
                target_player["poisoned_until"] = None  # 无期限，直到被新目标取代
                
                # 记录当前目标为下一晚的前一目标
                player["pukka_previous_target"] = target
                
                self.add_log(f"[夜间] {player['name']} (普卡) 选择 {target_player['name']} 中毒", "night")
        
        # 处理醉酒类行动（如侍臣让目标醉酒3天3夜）
        elif action_type == "drunk" and target:
            if target_player:
                duration = extra_data.get("duration", 3) if extra_data else 3  # 默认3天3夜
                target_player["drunk"] = True
                target_player["drunk_until"] = {
                    "day": self.day_number + duration,
                    "night": self.night_number + duration
                }
                self.add_log(f"[夜间] {player['name']} 使 {target_player['name']} 醉酒 {duration} 天", "night")
        
        # 处理水手的特殊醉酒（水手和目标中一人醉酒）
        elif action_type == "sailor_drunk" and target:
            if target_player and player:
                # 由说书人决定谁醉酒（通过 extra_data.drunk_choice）
                drunk_choice = extra_data.get("drunk_choice", "target") if extra_data else "target"
                drunk_player = target_player if drunk_choice == "target" else player
                
                drunk_player["drunk"] = True
                # 醉酒持续到明天黄昏
                drunk_player["drunk_until"] = {
                    "day": self.day_number + 1,
                    "night": self.night_number + 1
                }
                drunk_name = drunk_player['name']
                self.add_log(f"[夜间] {player['name']} (水手) 选择了 {target_player['name']}，{drunk_name} 喝醉了", "night")

        elif action_type == "gambler_guess" and target:
            if target_player and player:
                guessed_role_id = (extra_data or {}).get("guessed_role_id")
                actual_role = self._get_player_actual_role(target_player)
                actual_role_id = (actual_role or {}).get("id")
                guessed_role_name = (self._find_role_by_id(guessed_role_id) or {}).get("name", guessed_role_id or "未知")
                actual_role_name = (actual_role or {}).get("name", "未知")
                if guessed_role_id and guessed_role_id == actual_role_id:
                    self.add_log(f"[夜间] {player['name']} (赌徒) 猜测 {target_player['name']} 是 {guessed_role_name}，猜对了", "night")
                else:
                    if not any(d.get("player_id") == player_id and d.get("cause") == "赌徒猜错" for d in self.night_deaths):
                        self.night_deaths.append({
                            "player_id": player_id,
                            "player_name": player["name"],
                            "cause": "赌徒猜错"
                        })
                    self.add_log(f"[夜间] {player['name']} (赌徒) 猜测 {target_player['name']} 是 {guessed_role_name}，实际为 {actual_role_name}，赌徒死亡", "night")
        
        # 处理祖母选择孙子
        elif action_type == "grandchild_select" and target:
            if target_player:
                target_player["is_grandchild"] = True
                target_player["grandchild_of"] = player_id
                # 同时记录祖母知道孙子的角色
                player["grandchild_id"] = target
                self.add_log(f"[夜间] {player['name']} (祖母) 得知 {target_player['name']} 是她的孙子，角色是 {target_player['role']['name'] if target_player.get('role') else '未知'}", "night")
        
        # 处理管家选择主人
        elif action_type == "butler_master" and target:
            if target_player and player:
                player["butler_master_id"] = target
                player["butler_master_name"] = target_player["name"]
                self.add_log(f"[夜间] {player['name']} (管家) 选择 {target_player['name']} 作为主人", "night")
        
        # 更新日期: 2026-01-05 - 驱魔人选择目标
        elif action_type == "exorcist" and target:
            if target_player and player:
                # 记录驱魔人选择的目标
                if not hasattr(self, 'exorcist_previous_targets'):
                    self.exorcist_previous_targets = []
                
                self.exorcist_previous_targets = [target]
                
                # 检查驱魔人是否醉酒/中毒
                is_affected = player.get("drunk") or player.get("poisoned")
                
                if not is_affected:
                    # 检查目标是否是恶魔
                    if target_player.get("role_type") == "demon":
                        self.demon_exorcised_tonight = True
                        self.add_log(f"[夜间] {player['name']} (驱魔人) 选择了 {target_player['name']}，恶魔今晚无法行动！", "night")
                    else:
                        self.add_log(f"[夜间] {player['name']} (驱魔人) 选择了 {target_player['name']}，但目标不是恶魔", "night")
                else:
                    self.add_log(f"[夜间] {player['name']} (驱魔人) 选择了 {target_player['name']}（醉酒/中毒，能力无效）", "night")
        
        # 更新日期: 2026-01-05 - 恶魔代言人选择目标
        elif action_type == "devils_advocate" and target:
            if target_player and player:
                # 记录恶魔代言人选择的目标
                if not hasattr(self, 'devils_advocate_previous_targets'):
                    self.devils_advocate_previous_targets = []
                
                self.devils_advocate_previous_targets = [target]
                
                # 检查恶魔代言人是否醉酒/中毒
                is_affected = player.get("drunk") or player.get("poisoned")
                
                if not is_affected:
                    # 设置今天被保护的玩家
                    self.devils_advocate_protected = target
                    target_player["devils_advocate_protected"] = True
                    self.add_log(f"[夜间] {player['name']} (恶魔代言人) 选择保护 {target_player['name']}，明天无法被处决", "night")
                else:
                    self.add_log(f"[夜间] {player['name']} (恶魔代言人) 选择了 {target_player['name']}（醉酒/中毒，能力无效）", "night")
        
        # 更新日期: 2026-01-08 - 麻脸巫婆改变角色
        elif action_type == "pit_hag" and target:
            if target_player and player and extra_data:
                new_role_id = extra_data.get("new_role_id")
                
                # 检查麻脸巫婆是否醉酒/中毒
                is_affected = player.get("drunk") or player.get("poisoned")
                
                if not is_affected and new_role_id:
                    # 获取新角色信息
                    new_role = self._find_role_by_id(new_role_id)
                    new_role_type = self._get_role_type(new_role)
                    
                    if new_role:
                        old_role = target_player.get("role", {})
                        old_role_name = old_role.get("name", "未知") if old_role else "未知"
                        old_role_type = target_player.get("role_type")
                        
                        # 检查是否创造了新恶魔
                        created_demon = new_role_type == "demon" and old_role_type != "demon"
                        
                        # 改变目标的角色
                        target_player["role"] = new_role
                        target_player["role_type"] = new_role_type
                        
                        # 标记角色变更事件
                        if not hasattr(self, 'pit_hag_changes'):
                            self.pit_hag_changes = []
                        
                        change_info = {
                            "target_id": target,
                            "target_name": target_player["name"],
                            "old_role": old_role_name,
                            "new_role": new_role.get("name", "未知"),
                            "created_demon": created_demon
                        }
                        self.pit_hag_changes.append(change_info)
                        
                        if created_demon:
                            # 如果创造了新恶魔，标记需要说书人决定今晚的死亡
                            self.pit_hag_created_demon = True
                            self.add_log(f"[夜间] {player['name']} (麻脸巫婆) 将 {target_player['name']} 从 {old_role_name} 变为 {new_role['name']}！⚠️ 创造了新恶魔！", "night")
                        else:
                            self.add_log(f"[夜间] {player['name']} (麻脸巫婆) 将 {target_player['name']} 从 {old_role_name} 变为 {new_role['name']}", "night")
                    else:
                        self.add_log(f"[夜间] {player['name']} (麻脸巫婆) 选择的角色不存在", "night")
                else:
                    self.add_log(f"[夜间] {player['name']} (麻脸巫婆) 选择了目标（醉酒/中毒，能力无效）", "night")
        
        # 处理跳过行动
        elif action_type == "skip":
            self.add_log(f"[夜间] {player['name']} 选择不行动", "night")
        
        # 更新日期: 2026-01-12 - 处理信息类行动
        elif action_type == "info":
            if target_player:
                self.add_log(f"[夜间] {player['name']} 获取了关于 {target_player['name']} 的信息", "night")
            else:
                self.add_log(f"[夜间] {player['name']} 获取了信息", "night")
        
        # 其他行动
        elif player:
            target_text = f" -> {target_player['name']}" if target_player else ""
            self.add_log(f"[夜间] {player['name']} 执行了行动: {action}{target_text}", "night")
        
        # 标记一次性技能已使用（只要执行了行动且不是跳过）
        if player and action_type != "skip":
            role_id = player.get("role", {}).get("id", "") if player.get("role") else ""
            if role_id in once_per_game_roles:
                player["ability_used"] = True
                self.add_log(f"[系统] {player['name']} 的一次性技能已使用", "info")
    
    # 更新日期: 2026-01-02 - 添加小恶魔传刀功能
    def process_imp_suicide(self, imp_player_id):
        """处理小恶魔自杀传刀"""
        imp_player = next((p for p in self.players if p["id"] == imp_player_id), None)
        if not imp_player:
            return
        
        # 找到存活的爪牙
        alive_minions = [p for p in self.players if p["alive"] and p.get("role_type") == "minion"]
        
        if not alive_minions:
            self.add_log(f"[夜间] {imp_player['name']} (小恶魔) 自杀，但没有存活的爪牙可以传刀", "night")
            return
        
        # 随机选择一名爪牙成为新的小恶魔
        new_imp = random.choice(alive_minions)
        old_role = new_imp.get("role", {}).get("name", "未知")
        
        # 更新爪牙的角色为小恶魔
        new_imp["role"] = {
            "id": "imp",
            "name": "小恶魔"
        }
        new_imp["role_type"] = "demon"
        
        # 标记传刀事件
        if not hasattr(self, 'imp_starpass'):
            self.imp_starpass = []
        self.imp_starpass.append({
            "old_imp_id": imp_player_id,
            "old_imp_name": imp_player["name"],
            "new_imp_id": new_imp["id"],
            "new_imp_name": new_imp["name"],
            "old_role": old_role
        })
        
        self.add_log(f"🗡️ {imp_player['name']} (小恶魔) 自杀传刀！{new_imp['name']} (原{old_role}) 成为新的小恶魔！", "night")
    
    # 更新日期: 2026-01-05 - 茶艺师保护检查辅助函数
    def _is_protected_by_tea_lady(self, player_id):
        """检查玩家是否被茶艺师保护（茶艺师的存活善良邻居无法死亡）"""
        # 找到存活的茶艺师
        tea_lady = next(
            (p for p in self.players if p["alive"] and p.get("role", {}).get("id") == "tea_lady"),
            None
        )
        
        if not tea_lady:
            return False
        
        # 检查茶艺师是否醉酒/中毒
        if tea_lady.get("drunk") or tea_lady.get("poisoned"):
            return False
        
        # 获取茶艺师的座位索引
        tea_lady_seat = tea_lady.get("seat_number", 0)
        total_players = len(self.players)
        
        # 计算茶艺师的两个邻居（环形座位）
        left_seat = (tea_lady_seat - 2) % total_players + 1  # 左边邻居
        right_seat = tea_lady_seat % total_players + 1  # 右边邻居
        
        left_neighbor = next((p for p in self.players if p.get("seat_number") == left_seat), None)
        right_neighbor = next((p for p in self.players if p.get("seat_number") == right_seat), None)
        
        # 检查两个邻居是否都存活且都是善良的
        if not left_neighbor or not right_neighbor:
            return False
        
        if not left_neighbor["alive"] or not right_neighbor["alive"]:
            return False
        
        left_is_good = left_neighbor.get("role_type") in ["townsfolk", "outsider"]
        right_is_good = right_neighbor.get("role_type") in ["townsfolk", "outsider"]
        
        if not (left_is_good and right_is_good):
            return False
        
        # 检查目标玩家是否是茶艺师的邻居
        target_player = next((p for p in self.players if p["id"] == player_id), None)
        if not target_player:
            return False
        
        target_seat = target_player.get("seat_number", 0)
        
        # 如果目标是茶艺师的邻居，则被保护
        if target_seat == left_seat or target_seat == right_seat:
            return True
        
        return False

    def process_night_kills(self):
        """处理夜间击杀，考虑保护效果"""
        if not hasattr(self, 'demon_kills'):
            return []
        
        actual_deaths = []
        protected = getattr(self, 'protected_players', [])
        
        for kill in self.demon_kills:
            target_id = kill["target_id"]
            target_player = next((p for p in self.players if p["id"] == target_id), None)
            
            if not target_player:
                continue
            if not target_player.get("alive", True):
                continue
            force_death = bool(kill.get("ignore_protection"))
            killer_player = next((p for p in self.players if p["id"] == kill.get("killer_id")), None)
            killer_is_demon = bool(killer_player and killer_player.get("role_type") == "demon")
            kill_type = kill.get("kill_type")
            if kill_type == "assassin":
                death_cause = "刺客击杀"
            elif kill_type == "gossip":
                death_cause = "造谣者触发"
            else:
                death_cause = "恶魔击杀"
            if force_death:
                actual_deaths.append({
                    "player_id": target_id,
                    "player_name": target_player["name"],
                    "cause": death_cause
                })
                if target_player.get("role") and target_player["role"].get("id") == "ravenkeeper":
                    if not target_player.get("poisoned") and not target_player.get("drunk"):
                        target_player["ravenkeeper_triggered"] = True
                        self.add_log(f"守鸦人 {target_player['name']} 在夜间死亡，需要唤醒选择一名玩家", "night")
                if killer_is_demon:
                    grandmother_id = target_player.get("grandchild_of")
                    grandmother = next((p for p in self.players if p["id"] == grandmother_id), None) if grandmother_id else None
                    if grandmother and grandmother.get("alive", True) and self._is_ability_active(grandmother, "被动技能:grandmother"):
                        actual_deaths.append({
                            "player_id": grandmother["id"],
                            "player_name": grandmother["name"],
                            "cause": "祖母殉孙"
                        })
                        self.add_log(f"[夜间] 祖母 {grandmother['name']} 因孙子被恶魔杀死而死亡", "night")
                continue
            
            # 检查是否被保护
            if target_id in protected:
                self.add_log(f"{target_player['name']} 被保护，免疫了恶魔的击杀", "night")
                continue
            
            # 检查是否是士兵（恶魔无法杀死）
            if target_player.get("role") and target_player["role"].get("id") == "soldier":
                if self._is_ability_active(target_player, "被动技能:soldier"):
                    self.add_log(f"{target_player['name']} 是士兵，免疫了恶魔的击杀", "night")
                    continue
            
            # 更新日期: 2026-01-05 - 茶艺师保护检查
            # 检查目标是否被茶艺师保护（茶艺师存活的邻居且两邻居都是善良的）
            if self._is_protected_by_tea_lady(target_id):
                self.add_log(f"🍵 {target_player['name']} 被茶艺师保护，无法死亡", "night")
                continue
            
            # 更新日期: 2026-01-05 - 弄臣保护检查（首次死亡时不会死亡）
            if target_player.get("role") and target_player["role"].get("id") == "fool":
                if not target_player.get("fool_used") and not target_player.get("drunk") and not target_player.get("poisoned"):
                    target_player["fool_used"] = True
                    self.add_log(f"🃏 {target_player['name']} (弄臣) 首次死亡被避免！", "night")
                    continue
            
            # 检查是否是镇长（自动处理替死）
            if target_player.get("role") and target_player["role"].get("id") == "mayor":
                if self._is_ability_active(target_player, "被动技能:mayor_substitute"):
                    candidates = [p for p in self.players if p.get("alive", True) and p["id"] != target_id]
                    if candidates and random.random() < MAYOR_SUBSTITUTE_RATE:
                        substitute = random.choice(candidates)
                        actual_deaths.append({
                            "player_id": substitute["id"],
                            "player_name": substitute["name"],
                            "cause": "镇长替死"
                        })
                        self.add_log(f"🏛️ 镇长 {target_player['name']} 被恶魔选中，{substitute['name']} 替镇长死亡", "night")
                        if substitute.get("role") and substitute["role"].get("id") == "ravenkeeper":
                            if not substitute.get("poisoned") and not substitute.get("drunk"):
                                substitute["ravenkeeper_triggered"] = True
                                self.add_log(f"守鸦人 {substitute['name']} 在夜间死亡，需要唤醒选择一名玩家", "night")
                    else:
                        self.add_log(f"🏛️ 镇长 {target_player['name']} 被恶魔选中，替死未触发", "night")
                        actual_deaths.append({
                            "player_id": target_id,
                            "player_name": target_player["name"],
                            "cause": "恶魔击杀"
                        })
                    continue
            
            # 添加到死亡列表
            actual_deaths.append({
                "player_id": target_id,
                "player_name": target_player["name"],
                "cause": death_cause
            })
            
            # 检查是否是守鸦人（死亡时被唤醒）
            if target_player.get("role") and target_player["role"].get("id") == "ravenkeeper":
                if not target_player.get("poisoned") and not target_player.get("drunk"):
                    target_player["ravenkeeper_triggered"] = True
                    self.add_log(f"守鸦人 {target_player['name']} 在夜间死亡，需要唤醒选择一名玩家", "night")
            if killer_is_demon:
                grandmother_id = target_player.get("grandchild_of")
                grandmother = next((p for p in self.players if p["id"] == grandmother_id), None) if grandmother_id else None
                if grandmother and grandmother.get("alive", True) and self._is_ability_active(grandmother, "被动技能:grandmother"):
                    actual_deaths.append({
                        "player_id": grandmother["id"],
                        "player_name": grandmother["name"],
                        "cause": "祖母殉孙"
                    })
                    self.add_log(f"[夜间] 祖母 {grandmother['name']} 因孙子被恶魔杀死而死亡", "night")
        
        return actual_deaths
    
    def check_and_trigger_ravenkeeper(self, target_id):
        """在记录击杀行动后立即检查目标是否是守鸦人，如果是则触发其能力"""
        target_player = next((p for p in self.players if p["id"] == target_id), None)
        if not target_player:
            return
        
        is_ravenkeeper = (target_player.get("role") and 
                          target_player["role"].get("id") == "ravenkeeper")
        if not is_ravenkeeper:
            return
        
        # 检查是否被保护
        protected = getattr(self, 'protected_players', [])
        if target_id in protected:
            return
        
        # 检查是否是士兵（不会被杀）
        if target_player.get("role", {}).get("id") == "soldier":
            if self._is_ability_active(target_player, "被动技能:soldier"):
                return
        
        # 守鸦人未中毒/醉酒时触发（中毒/醉酒时也触发，但给假信息，由API层处理）
        if not target_player.get("ravenkeeper_triggered"):
            target_player["ravenkeeper_triggered"] = True
            target_player["ravenkeeper_choice_made"] = False
            target_player["ravenkeeper_result"] = None
            self.add_log(f"守鸦人 {target_player['name']} 在夜间死亡，等待玩家选择查验目标", "night")

    def check_ravenkeeper_trigger(self):
        """检查是否有守鸦人需要被唤醒（兼容说书人端调用）"""
        # 先处理夜间击杀（如果尚未处理），以确定谁死了、是否触发守鸦人
        if not getattr(self, '_night_kills_processed', False):
            self._pre_process_results = self.process_night_kills()
            self._night_kills_processed = True
        
        for death in getattr(self, 'demon_kills', []):
            target_id = death.get("target_id")
            target_player = next((p for p in self.players if p["id"] == target_id), None)
            if target_player and target_player.get("ravenkeeper_triggered"):
                return {
                    "triggered": True,
                    "player_id": target_id,
                    "player_name": target_player["name"],
                    "choice_made": target_player.get("ravenkeeper_choice_made", False),
                    "result": target_player.get("ravenkeeper_result")
                }
        return {"triggered": False}
    
    def add_night_death(self, player_id, cause="恶魔击杀"):
        """添加夜间死亡"""
        player = next((p for p in self.players if p["id"] == player_id), None)
        if player:
            self.night_deaths.append({
                "player_id": player_id,
                "player_name": player["name"],
                "cause": cause
            })
    
    def start_day(self):
        """开始白天"""
        self.reconcile_player_role_types("白天开始")
        self.day_number += 1
        self.current_phase = "day"
        self.nominations = []
        self.votes = {}
        self.day_leading_nomination_id = None
        self.day_leading_vote_count = 0
        self.day_tied = False
        
        # 更新日期: 2026-01-05 - 清除上一天的恶魔代言人保护
        self.devils_advocate_protected = None
        for p in self.players:
            p.pop("devils_advocate_protected", None)
        
        # 处理恶魔击杀（考虑保护），复用守鸦人检查时的预处理结果
        if getattr(self, '_night_kills_processed', False):
            demon_deaths = getattr(self, '_pre_process_results', [])
            self._night_kills_processed = False
            self._pre_process_results = None
        else:
            demon_deaths = self.process_night_kills()
        for death in demon_deaths:
            if death not in self.night_deaths:
                self.night_deaths.append(death)
        
        # 处理夜间死亡
        for death in self.night_deaths:
            player = next((p for p in self.players if p["id"] == death["player_id"]), None)
            if player:
                # 更新日期: 2026-01-05 - 僵怖假死逻辑
                # 检查是否是僵怖的第一次死亡
                is_zombuul = player.get("role") and player["role"].get("id") == "zombuul"
                is_first_death = not getattr(self, 'zombuul_first_death', False)
                is_affected = player.get("drunk") or player.get("poisoned")
                
                if is_zombuul and is_first_death and not is_affected:
                    # 僵怖第一次死亡 - 假死
                    player["appears_dead"] = True  # 看起来死了
                    player["alive"] = True  # 但实际还活着
                    self.zombuul_first_death = True
                    self.add_log(f"💀 {player['name']} 在夜间死亡（僵怖假死）", "death")
                else:
                    player["alive"] = False
                    self.add_log(f"{player['name']} 在夜间死亡 ({death['cause']})", "death")
                    
                    # 更新日期: 2026-01-05 - 月之子检查（夜间死亡时触发）
                    if player.get("role") and player["role"].get("id") == "moonchild":
                        if not player.get("drunk") and not player.get("poisoned"):
                            player["moonchild_triggered"] = True
                            self.pending_moonchild = player["id"]
                            self.add_log(f"🌙 月之子 {player['name']} 在夜间死亡，需要选择一名玩家", "game_event")
        
        self.add_log(f"第 {self.day_number} 天开始", "phase")
    
    def nominate(self, nominator_id, nominee_id):
        """提名"""
        nominator = next((p for p in self.players if p["id"] == nominator_id), None)
        nominee = next((p for p in self.players if p["id"] == nominee_id), None)
        
        if not nominator or not nominee:
            return {"success": False, "error": "无效的玩家"}
        
        if not nominator["alive"]:
            return {"success": False, "error": "死亡玩家不能提名"}
        if not nominee["alive"]:
            return {"success": False, "error": "死亡玩家不能被提名"}
        
        # 检查是否已经提名过
        for nom in self.nominations:
            if nom["nominator_id"] == nominator_id:
                return {"success": False, "error": "该玩家今天已经提名过"}
            if nom["nominee_id"] == nominee_id:
                return {"success": False, "error": "该玩家今天已经被提名过"}
        
        nomination = {
            "id": len(self.nominations) + 1,
            "nominator_id": nominator_id,
            "nominator_name": nominator["name"],
            "nominee_id": nominee_id,
            "nominee_name": nominee["name"],
            "votes": [],
            "vote_count": 0,
            "status": "pending"
        }
        
        self.nominations.append(nomination)
        self.add_log(f"{nominator['name']} 提名了 {nominee['name']}", "nomination")
        
        # 检查贞洁者能力触发
        virgin_triggered = False
        nominee_role_id = nominee.get("role", {}).get("id") if nominee.get("role") else None
        
        # 如果被提名者是贞洁者，且能力未使用，且提名者是镇民
        if (nominee_role_id == "virgin" and 
            not nominee.get("virgin_ability_used", False) and
            self._registers_as_townsfolk_for_good_info(nominator, "贞洁者")):
            if self._is_ability_active(nominee, "被动技能:virgin"):
            
                # 标记贞洁者能力已使用
                nominee["virgin_ability_used"] = True
                
                # 提名者立即被处决
                nominator["alive"] = False
                
                # 记录处决
                self.executions.append({
                    "day": self.day_number,
                    "executed_id": nominator_id,
                    "executed_name": nominator["name"],
                    "reason": "virgin_ability",
                    "vote_count": 0,
                    "required_votes": 0
                })
                
                virgin_triggered = True
                self.add_log(f"⚡ 贞洁者能力触发！{nominator['name']} 是镇民，立即被处决！", "execution")
                
                # 更新提名状态
                nomination["status"] = "virgin_triggered"
            else:
                self.add_log(f"[系统] 贞洁者 {nominee['name']} 能力未触发（醉酒或中毒失效）", "info")
        
        return {
            "success": True, 
            "nomination": nomination,
            "virgin_triggered": virgin_triggered,
            "executed_player": nominator["name"] if virgin_triggered else None
        }
    
    def vote(self, nomination_id, voter_id, vote_value):
        """投票"""
        nomination = next((n for n in self.nominations if n["id"] == nomination_id), None)
        voter = next((p for p in self.players if p["id"] == voter_id), None)
        
        if not nomination or not voter:
            return {"success": False, "error": "无效的提名或玩家"}
        
        # 检查是否已经投过票
        for v in nomination["votes"]:
            if v["voter_id"] == voter_id:
                return {"success": False, "error": "该玩家已经投过票"}
        
        # 死亡玩家只有一次投票机会（弃票令牌）
        if not voter["alive"] and not voter["vote_token"]:
            return {"success": False, "error": "该死亡玩家已经使用过投票令牌"}
        
        # 管家投票限制：只有当主人投票时才能投票
        if voter.get("role", {}).get("id") == "butler" and voter.get("butler_master_id") and vote_value and self._is_ability_active(voter, "被动技能:butler_vote"):
            master_id = voter["butler_master_id"]
            # 检查主人是否已经在这次提名中投了赞成票
            master_voted = False
            for v in nomination["votes"]:
                if v["voter_id"] == master_id and v["vote"]:
                    master_voted = True
                    break
            if not master_voted:
                master_name = voter.get("butler_master_name", "主人")
                return {"success": False, "error": f"管家只能在主人（{master_name}）投赞成票后才能投赞成票"}
        
        vote_record = {
            "voter_id": voter_id,
            "voter_name": voter["name"],
            "vote": vote_value,  # True = 赞成, False = 反对
            "voter_alive": voter["alive"]
        }
        
        nomination["votes"].append(vote_record)
        if vote_value:
            nomination["vote_count"] += 1
            
        # 死亡玩家投票后消耗令牌
        if not voter["alive"] and vote_value:
            voter["vote_token"] = False
        
        vote_text = "赞成" if vote_value else "反对"
        self.add_log(f"{voter['name']} 对 {nomination['nominee_name']} 投了{vote_text}票", "vote")
        return {"success": True}
    
    # 更新日期: 2026-01-02 - 修复圣徒能力，添加红唇女郎处决后检测
    def execute(self, nomination_id):
        """执行处决"""
        nomination = next((n for n in self.nominations if n["id"] == nomination_id), None)
        if not nomination:
            return {"success": False, "error": "无效的提名"}
        
        nominee = next((p for p in self.players if p["id"] == nomination["nominee_id"]), None)
        if not nominee:
            return {"success": False, "error": "无效的被提名者"}
        if not nominee.get("alive", True):
            return {"success": False, "error": "被提名者已死亡"}
        
        # 计算需要的票数（存活玩家的一半）
        alive_count = len([p for p in self.players if p["alive"]])
        required_votes = (alive_count // 2) + 1
        
        if nomination["vote_count"] >= required_votes:
            if self.mastermind_pending and self.day_number >= (self.mastermind_resolution_day or 10**9):
                nomination["status"] = "executed"
                self.executions.append({
                    "day": self.day_number,
                    "executed_id": nominee["id"],
                    "executed_name": nominee["name"],
                    "vote_count": nomination["vote_count"],
                    "required_votes": required_votes,
                    "reason": "mastermind_final_day"
                })
                loser_is_evil = self._is_player_evil_for_win(nominee)
                winner = "good" if loser_is_evil else "evil"
                self.mastermind_pending = False
                self.mastermind_resolution_day = None
                self.mastermind_forced_winner = winner
                self.add_log(f"🧠 主谋结算：{nominee['name']} 被处决，{winner} 阵营获胜", "game_end")
                return {
                    "success": True,
                    "executed": False,
                    "player": nominee,
                    "mastermind_resolution": True,
                    "game_end": {"ended": True, "winner": winner, "reason": "主谋结算"}
                }

            # 更新日期: 2026-01-05 - 恶魔代言人保护检查
            # 检查被提名者是否被恶魔代言人保护
            if nominee.get("devils_advocate_protected"):
                nomination["status"] = "protected"
                # 清除保护标记（只保护一次处决）
                nominee["devils_advocate_protected"] = False
                self.add_log(f"🛡️ {nominee['name']} 被恶魔代言人保护，免于处决", "execution")
                return {
                    "success": True, 
                    "executed": False, 
                    "protected_by_devils_advocate": True,
                    "player": nominee
                }
            
            # 更新日期: 2026-01-05 - 和平主义者能力检查
            # 检查是否有和平主义者且被处决者是善良玩家
            nominee_is_good = nominee.get("role_type") in ["townsfolk", "outsider"]
            if nominee_is_good:
                pacifist = next(
                    (p for p in self.players if p["alive"] and p.get("role", {}).get("id") == "pacifist"),
                    None
                )
                if pacifist:
                    # 检查和平主义者是否醉酒/中毒
                    pacifist_affected = pacifist.get("drunk") or pacifist.get("poisoned")
                    if not pacifist_affected:
                        # 由说书人决定是否让玩家存活 - 这里标记需要说书人决定
                        # 我们返回一个特殊状态让前端处理
                        return {
                            "success": True,
                            "executed": False,
                            "pacifist_intervention": True,
                            "pacifist_name": pacifist["name"],
                            "nominee_id": nominee["id"],
                            "nominee_name": nominee["name"],
                            "vote_count": nomination["vote_count"],
                            "required_votes": required_votes,
                            "nomination_id": nomination["id"]
                        }
            
            # 更新日期: 2026-01-05 - 弄臣保护检查（首次死亡时不会死亡）
            is_fool = nominee.get("role") and nominee["role"].get("id") == "fool"
            if is_fool and not nominee.get("fool_used") and not nominee.get("drunk") and not nominee.get("poisoned"):
                nominee["fool_used"] = True
                nomination["status"] = "fool_saved"
                self.add_log(f"🃏 {nominee['name']} (弄臣) 首次死亡被避免！", "execution")
                return {
                    "success": True,
                    "executed": False,
                    "fool_saved": True,
                    "player": nominee
                }
            
            # 记录被处决者的角色类型（用于后续检查红唇女郎）
            was_demon = nominee.get("role_type") == "demon"
            was_minion = nominee.get("role_type") == "minion"
            
            # 更新日期: 2026-01-05 - 僵怖假死逻辑（处决时）
            # 检查是否是僵怖的第一次死亡
            is_zombuul = nominee.get("role") and nominee["role"].get("id") == "zombuul"
            is_first_death = not getattr(self, 'zombuul_first_death', False)
            is_affected = nominee.get("drunk") or nominee.get("poisoned")
            
            if is_zombuul and is_first_death and not is_affected:
                # 僵怖第一次被处决 - 假死
                nominee["appears_dead"] = True  # 看起来死了
                nominee["alive"] = True  # 但实际还活着
                self.zombuul_first_death = True
                nomination["status"] = "executed"
                self.executions.append({
                    "day": self.day_number,
                    "executed_id": nominee["id"],
                    "executed_name": nominee["name"],
                    "vote_count": nomination["vote_count"],
                    "required_votes": required_votes
                })
                self.add_log(f"💀 {nominee['name']} 被处决（僵怖假死）", "execution")
                return {
                    "success": True, 
                    "executed": True, 
                    "player": nominee,
                    "zombuul_fake_death": True
                }
            
            nominee["alive"] = False
            nomination["status"] = "executed"
            self.executions.append({
                "day": self.day_number,
                "executed_id": nominee["id"],
                "executed_name": nominee["name"],
                "vote_count": nomination["vote_count"],
                "required_votes": required_votes
            })
            self.add_log(f"{nominee['name']} 被处决 (获得 {nomination['vote_count']}/{required_votes} 票)", "execution")
            if was_minion:
                minstrel = next(
                    (p for p in self.players if p.get("alive", True) and p.get("role", {}).get("id") == "minstrel"),
                    None
                )
                if minstrel and self._is_ability_active(minstrel, "被动技能:minstrel"):
                    self.minstrel_effect_until_day = self.day_number + 1
                    self.add_log(f"🎻 吟游诗人 {minstrel['name']} 触发：其他玩家醉酒至第 {self.minstrel_effect_until_day} 天黄昏", "game_event")
            
            # 检查圣徒能力：如果被处决的是圣徒，邪恶阵营获胜
            nominee_role_id = nominee.get("role", {}).get("id") if nominee.get("role") else None
            
            # 圣徒判定：必须是真正的圣徒角色，且没有醉酒/中毒
            if nominee_role_id == "saint":
                if self._is_ability_active(nominee, "被动技能:saint"):
                    self.add_log(f"⚡ 圣徒 {nominee['name']} 被处决！邪恶阵营获胜！", "game_end")
                    return {
                        "success": True, 
                        "executed": True, 
                        "player": nominee,
                        "saint_executed": True,
                        "game_end": {"ended": True, "winner": "evil", "reason": "圣徒被处决"}
                    }
                self.add_log(f"[系统] 圣徒 {nominee['name']} 能力失效", "info")
            
            # 更新日期: 2026-01-05 - 月之子检查（处决死亡时触发）
            if nominee_role_id == "moonchild":
                is_affected = nominee.get("drunk") or nominee.get("poisoned")
                if not is_affected:
                    nominee["moonchild_triggered"] = True
                    self.pending_moonchild = nominee["id"]
                    self.add_log(f"🌙 月之子 {nominee['name']} 被处决，需要选择一名玩家", "game_event")
            
            # 如果被处决的是恶魔，检查红唇女郎能力
            result = {"success": True, "executed": True, "player": nominee}
            
            # 添加月之子触发信息
            if nominee.get("moonchild_triggered"):
                result["moonchild_triggered"] = True
                result["moonchild_id"] = nominee["id"]
                result["moonchild_name"] = nominee["name"]
            
            if was_demon:
                game_end = self.check_game_end(apply_scarlet_woman=True)
                if game_end.get("ended") and game_end.get("winner") == "good":
                    if self._arm_mastermind():
                        game_end = self.check_game_end(apply_scarlet_woman=True)
                if game_end.get("scarlet_woman_triggered"):
                    result["scarlet_woman_triggered"] = True
                    result["new_demon_name"] = game_end.get("new_demon")
                if game_end.get("mastermind_pending"):
                    result["mastermind_triggered"] = True
                    result["mastermind_resolution_day"] = game_end.get("resolution_day")
                result["game_end"] = game_end
            
            return result
        else:
            nomination["status"] = "failed"
            self.add_log(f"{nominee['name']} 未被处决 (获得 {nomination['vote_count']}/{required_votes} 票)", "execution")
            return {"success": True, "executed": False}

    def declare_slayer_shot(self, shooter_id, target_id):
        shooter = next((p for p in self.players if p["id"] == shooter_id), None)
        target = next((p for p in self.players if p["id"] == target_id), None)
        if not shooter or not target:
            return {"success": False, "error": "无效的玩家"}
        if self.current_phase != "day":
            return {"success": False, "error": "当前不是白天"}
        if not shooter.get("alive", True):
            return {"success": False, "error": "死亡玩家不能发动公开开枪"}
        if not target.get("alive", True):
            return {"success": False, "error": "目标玩家已死亡"}

        is_real_slayer = shooter.get("role", {}).get("id") == "slayer"
        recluse_register_as_demon = (
            (target.get("role") or {}).get("id") == "recluse"
            and self._is_ability_active(target, "被动技能:recluse_slayer")
            and random.random() < RECLUSE_SLAYER_DEMON_REGISTRATION_RATE
        )
        is_demon_target = target.get("role_type") == "demon" or recluse_register_as_demon
        ability_active = self._is_ability_active(shooter, "白天技能:slayer")
        is_affected = not ability_active
        can_use_real_ability = is_real_slayer and not shooter.get("ability_used", False)

        if can_use_real_ability:
            shooter["ability_used"] = True

        result = {
            "success": True,
            "shooter_name": shooter["name"],
            "target_name": target["name"],
            "is_real_slayer": is_real_slayer,
            "target_died": False,
            "public_message": "无事发生"
        }

        if can_use_real_ability and not is_affected and is_demon_target:
            target["alive"] = False
            if recluse_register_as_demon:
                self.add_log(f"[系统提示] 陌客 {target['name']} 在枪手技能中被注册为恶魔", "info")
            self.add_log(f"🗡️ {shooter['name']} 公开宣告枪手开枪，{target['name']} 死亡", "death")
            result["target_died"] = True
            result["public_message"] = f"{target['name']} 死亡"
            result["game_end"] = self.check_game_end(apply_scarlet_woman=True)
            return result

        self.add_log(f"🗡️ {shooter['name']} 公开宣告枪手开枪，目标 {target['name']}，无事发生", "ability")
        return result

    def use_ability(self, player_id, target_id):
        player = next((p for p in self.players if p["id"] == player_id), None)
        target = next((p for p in self.players if p["id"] == target_id), None)
        if not player or not target:
            return {"success": False, "error": "无效的玩家"}
        if not player.get("alive", True):
            return {"success": False, "error": "死亡玩家不能使用技能"}
        if not target.get("alive", True):
            return {"success": False, "error": "目标玩家已死亡"}

        role = player.get("role", {}) or {}
        role_id = role.get("id", "")

        if self.current_phase != "day":
            return {"success": False, "error": "当前阶段不能使用技能"}

        if role_id == "slayer":
            return self.declare_slayer_shot(player_id, target_id)

        self.add_log(f"{player['name']} 对 {target['name']} 发动技能，无事发生", "game_event")
        return {
            "success": True,
            "target_died": False,
            "public_message": f"{player['name']} 对 {target['name']} 发动技能，无事发生"
        }
    
    def _alive_players_for_win_check(self):
        alive_players = [p for p in self.players if p["alive"]]
        alive_non_travelers = [p for p in alive_players if p.get("role_type") != "traveler"]
        return alive_players, alive_non_travelers

    def _had_execution_today(self):
        return any(e.get("day") == self.day_number for e in self.executions)

    def _has_active_mayor(self):
        mayor = next((p for p in self.players if p["alive"] and p.get("role", {}).get("id") == "mayor"), None)
        if not mayor:
            return False
        return self._is_ability_active(mayor, "被动技能:mayor")

    def _is_player_evil_for_win(self, player):
        if not player:
            return False
        role_type = player.get("role_type")
        if role_type in {"demon", "minion"}:
            return True
        if role_type == "traveler":
            return self._is_evil_traveler(player)
        alignment = str(player.get("alignment") or player.get("goon_alignment") or "").lower()
        return alignment in {"evil", "minion", "demon"} or bool(player.get("evil", False))

    def _arm_mastermind(self):
        if self.mastermind_pending or self.mastermind_forced_winner:
            return False
        mastermind = next(
            (p for p in self.players if p.get("alive", True) and p.get("role", {}).get("id") == "mastermind"),
            None
        )
        if not mastermind:
            return False
        if not self._is_ability_active(mastermind, "被动技能:mastermind"):
            return False
        self.mastermind_pending = True
        self.mastermind_resolution_day = self.day_number + 1
        self.add_log(f"🧠 主谋 {mastermind['name']} 触发：游戏延长到第 {self.mastermind_resolution_day} 天", "game_event")
        return True

    # 更新日期: 2026-01-02 - 添加红唇女郎能力检测
    def check_game_end(self, apply_scarlet_woman=False, allow_mayor_day_end=False):
        """检查游戏是否结束"""
        if self.mastermind_forced_winner:
            return {"ended": True, "winner": self.mastermind_forced_winner, "reason": "主谋结算完成"}
        alive_players, alive_non_travelers = self._alive_players_for_win_check()
        demons_alive = [p for p in alive_non_travelers if p.get("role_type") == "demon"]

        # 恶魔死亡（优先于邪恶人数胜利，符合同时满足时善良胜）
        if not demons_alive:
            if self.mastermind_pending:
                return {"ended": False, "mastermind_pending": True, "resolution_day": self.mastermind_resolution_day}
            if apply_scarlet_woman:
                scarlet_woman_result = self.check_scarlet_woman_trigger()
                if scarlet_woman_result["triggered"]:
                    return {"ended": False, "scarlet_woman_triggered": True, "new_demon": scarlet_woman_result["new_demon_name"]}
            return {"ended": True, "winner": "good", "reason": "恶魔已被消灭"}

        # 镇长胜利：仅在白天结束转夜晚时检查，且仅统计非旅行者人数
        if allow_mayor_day_end and len(alive_non_travelers) == 3 and not self._had_execution_today() and self._has_active_mayor():
            return {"ended": True, "winner": "good", "reason": "镇长在三人生还且无人处决时获胜"}

        # 只剩2名非旅行者玩家且恶魔存活，邪恶获胜
        if len(alive_non_travelers) <= 2:
            return {"ended": True, "winner": "evil", "reason": "邪恶势力占领了小镇"}

        return {"ended": False}
    
    # 更新日期: 2026-01-02 - 红唇女郎能力实现
    def check_scarlet_woman_trigger(self):
        """检查红唇女郎是否触发能力"""
        alive_players, alive_non_travelers = self._alive_players_for_win_check()
        
        # 红唇女郎能力条件：存活玩家>=5人
        if len(alive_non_travelers) < 5:
            self.add_log(f"[系统] 非旅行者存活不足5人（当前{len(alive_non_travelers)}人），红唇女郎能力无法触发", "info")
            return {"triggered": False}
        
        # 找到存活的红唇女郎
        scarlet_woman = next(
            (p for p in alive_players if p.get("role", {}).get("id") == "scarlet_woman"),
            None
        )
        
        if not scarlet_woman:
            return {"triggered": False}
        
        if not self._is_ability_active(scarlet_woman, "被动技能:scarlet_woman"):
            self.add_log(f"[系统] 红唇女郎 {scarlet_woman['name']} 能力无法触发", "info")
            return {"triggered": False}
        
        # 找到刚死亡的恶魔角色
        dead_demon = next(
            (p for p in self.players if not p["alive"] and p.get("role_type") == "demon"),
            None
        )
        
        demon_role = dead_demon.get("role", {}) if dead_demon else {"id": "imp", "name": "小恶魔"}
        
        # 红唇女郎成为恶魔
        scarlet_woman["role"] = demon_role
        scarlet_woman["role_type"] = "demon"
        
        self.add_log(f"💋 红唇女郎 {scarlet_woman['name']} 继承了恶魔身份！成为 {demon_role.get('name', '恶魔')}！", "game_event")
        
        return {
            "triggered": True,
            "new_demon_id": scarlet_woman["id"],
            "new_demon_name": scarlet_woman["name"]
        }
    
    def update_player_status(self, player_id, status_type, value):
        """更新玩家状态"""
        player = next((p for p in self.players if p["id"] == player_id), None)
        if player and status_type in ["poisoned", "drunk", "protected", "alive"]:
            player[status_type] = value
            status_text = "是" if value else "否"
            self.add_log(f"更新 {player['name']} 的 {status_type} 状态为 {status_text}", "status")
            return {"success": True}
        return {"success": False, "error": "无效的玩家或状态"}

    def _roll_poison_failure(self, player, scene=""):
        if not player or not player.get("poisoned", False):
            return False
        failed = random.random() < POISON_FAILURE_RATE
        self.add_log(f"[系统] {player['name']} 中毒判定：{'失效' if failed else '生效'}（{scene or '技能'}）", "info")
        return failed

    def _is_ability_active(self, player, scene=""):
        if not player:
            return False
        player_role_id = (player.get("role") or {}).get("id")
        if self.minstrel_effect_until_day is not None and self.day_number <= self.minstrel_effect_until_day and player_role_id != "minstrel":
            self.add_log(f"[系统] {player['name']} 受吟游诗人效果影响，能力失效（{scene or '技能'}）", "info")
            return False
        if player.get("drunk", False):
            self.add_log(f"[系统] {player['name']} 醉酒导致能力失效（{scene or '技能'}）", "info")
            return False
        return not self._roll_poison_failure(player, scene)

    def _spy_registers_as_good_for_info(self, target_player, scene=""):
        if not target_player:
            return False
        role_id = (target_player.get("role") or {}).get("id")
        if role_id != "spy":
            return False
        disguised = random.random() < SPY_GOOD_REGISTRATION_RATE
        self.add_log(f"[系统] 间谍 {target_player['name']} 注册判定：{'伪装为善良' if disguised else '暴露邪恶'}（{scene or '信息'}）", "info")
        return disguised

    def _registers_as_evil_for_good_info(self, target_player, scene=""):
        if not target_player:
            return False
        role_id = (target_player.get("role") or {}).get("id")
        if role_id == "recluse":
            if self._is_ability_active(target_player, f"被动技能:recluse_{scene or 'info'}") and random.random() < RECLUSE_EVIL_REGISTRATION_RATE:
                self.add_log(f"[系统提示] 陌客 {target_player['name']} 在{scene or '信息'}中被注册为邪恶", "info")
                return True
            return False
        role_type = target_player.get("role_type")
        if role_type not in ["minion", "demon"]:
            return False
        if role_id == "spy":
            return not self._spy_registers_as_good_for_info(target_player, scene)
        return True

    def _registers_as_minion_for_good_info(self, target_player, scene=""):
        if not target_player:
            return False
        role_id = (target_player.get("role") or {}).get("id")
        if role_id == "recluse":
            if self._is_ability_active(target_player, f"被动技能:recluse_{scene or 'info'}") and random.random() < RECLUSE_MINION_REGISTRATION_RATE:
                self.add_log(f"[系统提示] 陌客 {target_player['name']} 在{scene or '信息'}中被注册为爪牙", "info")
                return True
            return False
        if target_player.get("role_type") != "minion":
            return False
        if role_id == "spy":
            return not self._spy_registers_as_good_for_info(target_player, scene)
        return True

    def _registers_as_townsfolk_for_good_info(self, target_player, scene=""):
        if not target_player:
            return False
        role_id = (target_player.get("role") or {}).get("id")
        if role_id == "spy":
            return self._spy_registers_as_good_for_info(target_player, scene)
        return target_player.get("role_type") == "townsfolk"

    def _is_evil_traveler(self, player):
        if not player or player.get("role_type") != "traveler":
            return False
        alignment = str(
            player.get("alignment")
            or player.get("traveler_alignment")
            or player.get("team")
            or player.get("goon_alignment")
            or ""
        ).lower()
        if alignment in {"evil", "minion", "demon"}:
            return True
        return bool(player.get("evil", False))

    def _is_recluse_between_demon_and_evil_traveler(self, recluse_player):
        if not recluse_player or (recluse_player.get("role") or {}).get("id") != "recluse":
            return False
        idx = next((i for i, p in enumerate(self.players) if p["id"] == recluse_player["id"]), -1)
        if idx < 0 or not self.players:
            return False
        left = self.players[(idx - 1) % len(self.players)]
        right = self.players[(idx + 1) % len(self.players)]
        neighbors = [left, right]
        has_demon_neighbor = any(n.get("role_type") == "demon" for n in neighbors)
        has_evil_traveler_neighbor = any(self._is_evil_traveler(n) for n in neighbors)
        return has_demon_neighbor and has_evil_traveler_neighbor

    def _spy_fake_good_role_name(self):
        good_roles = []
        for role_type in ["townsfolk", "outsider"]:
            good_roles.extend([r.get("name") for r in self.script.get("roles", {}).get(role_type, []) if r.get("name")])
        if not good_roles:
            return "镇民"
        return random.choice(good_roles)

    def _registered_role_name_for_good_info(self, target_player, scene=""):
        if not target_player:
            return "未知"
        if target_player.get("is_the_drunk") and target_player.get("true_role"):
            real_role_name = target_player["true_role"]["name"]
        else:
            real_role_name = (target_player.get("role") or {}).get("name", "未知")
        role_id = (target_player.get("role") or {}).get("id")
        if role_id == "recluse" and scene in {"殡仪员", "送葬者", "undertaker"}:
            if self._is_ability_active(target_player, "被动技能:recluse_undertaker") and random.random() < RECLUSE_UNDERTAKER_IMP_REGISTRATION_RATE:
                self.add_log(f"[系统提示] 陌客 {target_player['name']} 在{scene}信息中被注册为小恶魔", "info")
                return "小恶魔"
        if (target_player.get("role") or {}).get("id") == "spy" and self._spy_registers_as_good_for_info(target_player, scene):
            return self._spy_fake_good_role_name()
        return real_role_name

    def _all_role_names(self):
        names = []
        for group in self.script.get("roles", {}).values():
            for role in group:
                if role and role.get("name"):
                    names.append(role["name"])
        return names

    def _random_other_int(self, current, min_value, max_value):
        candidates = [v for v in range(min_value, max_value + 1) if v != current]
        if not candidates:
            return current
        return random.choice(candidates)

    def _random_other_role_name(self, current_role_name=None):
        role_names = self._all_role_names()
        candidates = [r for r in role_names if r != current_role_name]
        if not candidates:
            return current_role_name or "未知"
        return random.choice(candidates)

    def _distort_info_for_malfunction(self, info):
        if not isinstance(info, dict):
            return info
        message = info.get("message", "")
        if "请选择" in message or "无法" in message:
            return info

        distorted = dict(info)
        info_type = distorted.get("info_type")

        if info_type == "fortune_teller" and "has_demon" in distorted:
            new_value = not bool(distorted.get("has_demon"))
            distorted["has_demon"] = new_value
            target_text = message
            if message.startswith("在 ") and " 中，" in message:
                target_text = message.split(" 中，")[0][2:]
            distorted["message"] = f"在 {target_text} 中，{'有' if new_value else '没有'}恶魔"
            return distorted

        if info_type == "chef" and "pairs" in distorted:
            new_value = self._random_other_int(int(distorted.get("pairs", 0)), 0, max(1, len(self.players) // 2))
            distorted["pairs"] = new_value
            distorted["message"] = f"有 {new_value} 对邪恶玩家相邻"
            return distorted

        if info_type == "empath" and "evil_count" in distorted:
            new_value = self._random_other_int(int(distorted.get("evil_count", 0)), 0, 2)
            distorted["evil_count"] = new_value
            distorted["message"] = f"你的存活邻居中有 {new_value} 个是邪恶的"
            return distorted

        if info_type == "oracle" and "evil_dead_count" in distorted:
            new_value = self._random_other_int(int(distorted.get("evil_dead_count", 0)), 0, max(1, len([p for p in self.players if not p.get("alive", True)])))
            distorted["evil_dead_count"] = new_value
            distorted["message"] = f"死亡玩家中有 {new_value} 个是邪恶的"
            return distorted

        if info_type == "clockmaker" and "distance" in distorted:
            new_value = self._random_other_int(int(distorted.get("distance", 0)), 0, max(1, len(self.players) - 1))
            distorted["distance"] = new_value
            distorted["message"] = f"恶魔和最近的爪牙之间相隔 {new_value} 步"
            return distorted

        if info_type == "chambermaid" and "woke_count" in distorted:
            new_value = self._random_other_int(int(distorted.get("woke_count", 0)), 0, 2)
            distorted["woke_count"] = new_value
            if message.startswith("在 ") and " 中，" in message:
                target_text = message.split(" 中，")[0][2:]
                distorted["message"] = f"在 {target_text} 中，有 {new_value} 人今晚因自己的能力而被唤醒"
            return distorted

        if info_type == "seamstress" and "same_team" in distorted:
            new_value = not bool(distorted.get("same_team"))
            distorted["same_team"] = new_value
            if " 和 " in message and (" 是同一阵营" in message or " 不是同一阵营" in message):
                target_text = message.split(" 是同一阵营")[0].split(" 不是同一阵营")[0]
                distorted["message"] = f"{target_text} {'是' if new_value else '不是'}同一阵营"
            return distorted

        if info_type == "flowergirl" and "demon_nominated" in distorted:
            new_value = not bool(distorted.get("demon_nominated"))
            distorted["demon_nominated"] = new_value
            distorted["message"] = f"恶魔昨天{'提名了' if new_value else '没有提名'}"
            return distorted

        if info_type == "undertaker" and "executed_role" in distorted:
            fake_role = self._random_other_role_name(distorted.get("executed_role"))
            distorted["executed_role"] = fake_role
            prefix = message.split(" 的角色是 ")[0] if " 的角色是 " in message else "昨天被处决的玩家"
            distorted["message"] = f"{prefix} 的角色是 {fake_role}"
            return distorted

        if info_type == "ravenkeeper" and "target_role" in distorted:
            fake_role = self._random_other_role_name(distorted.get("target_role"))
            distorted["target_role"] = fake_role
            target_name = message.split(" 的角色是 ")[0] if " 的角色是 " in message else "该玩家"
            distorted["message"] = f"{target_name} 的角色是 {fake_role}"
            return distorted

        if info_type == "dreamer" and isinstance(distorted.get("roles"), list):
            roles = list(distorted.get("roles", []))
            if roles:
                roles[random.randrange(len(roles))] = self._random_other_role_name(roles[0] if len(roles) == 1 else random.choice(roles))
                distorted["roles"] = roles
                if " 的角色是 " in message and " 其中之一" in message:
                    target_name = message.split(" 的角色是 ")[0]
                    distorted["message"] = f"{target_name} 的角色是 {roles[0]} 或 {roles[1] if len(roles) > 1 else roles[0]} 其中之一"
            return distorted

        if info_type == "spy" and isinstance(distorted.get("grimoire"), list):
            fake_grimoire = []
            for line in distorted["grimoire"]:
                player_name = line.split("：")[0] if "：" in line else line
                fake_role = self._random_other_role_name()
                fake_grimoire.append(f"{player_name}：{fake_role}")
            distorted["grimoire"] = fake_grimoire
            distorted["message"] = "魔典信息：\n" + "\n".join(fake_grimoire)
            return distorted

        return distorted

    def _distort_info_for_poison(self, info):
        return self._distort_info_for_malfunction(info)
    
    def generate_info(self, player_id, info_type, targets=None):
        """生成角色信息"""
        player = next((p for p in self.players if p["id"] == player_id), None)
        if not player or not player["role"]:
            return None
        
        role = player["role"]
        role_id = role["id"]
        
        is_drunk = player.get("drunk", False)
        is_poisoned = player.get("poisoned", False)
        is_drunk_or_poisoned = is_drunk or is_poisoned
        poison_failed = self._roll_poison_failure(player, f"信息:{role_id}") if is_poisoned else False
        info_malfunctioned = is_drunk or poison_failed
        
        # 获取目标玩家
        target_players = []
        if targets:
            for tid in targets:
                tp = next((p for p in self.players if p["id"] == tid), None)
                if tp:
                    target_players.append(tp)
        
        # 根据角色类型生成信息
        info = None
        if role_id == "washerwoman":
            info = self._generate_washerwoman_info(player, False)
        elif role_id == "librarian":
            info = self._generate_librarian_info(player, False)
        elif role_id == "investigator":
            info = self._generate_investigator_info(player, False)
        elif role_id == "chef":
            info = self._generate_chef_info(player, False)
        elif role_id == "empath":
            info = self._generate_empath_info(player, False)
        elif role_id == "fortune_teller":
            info = self._generate_fortune_teller_info(player, target_players, False)
        elif role_id == "clockmaker":
            info = self._generate_clockmaker_info(player, False)
        elif role_id == "chambermaid":
            info = self._generate_chambermaid_info(player, target_players, False)
        elif role_id == "seamstress":
            info = self._generate_seamstress_info(player, target_players, False)
        elif role_id == "dreamer":
            info = self._generate_dreamer_info(player, target_players, False)
        elif role_id == "undertaker":
            info = self._generate_undertaker_info(player, False)
        elif role_id == "ravenkeeper":
            info = self._generate_ravenkeeper_info(player, target_players, False)
        elif role_id == "oracle":
            info = self._generate_oracle_info(player, False)
        elif role_id == "flowergirl":
            info = self._generate_flowergirl_info(player, False)
        elif role_id == "spy":
            info = self._generate_spy_info(player, False)

        if info is None:
            info = {"message": f"请根据 {role['name']} 的能力自行提供信息"}

        if info_malfunctioned:
            info = self._distort_info_for_malfunction(info)

        if isinstance(info, dict):
            info["is_drunk_or_poisoned"] = is_drunk_or_poisoned
        return info
    
    def _generate_washerwoman_info(self, player, is_drunk_or_poisoned=False):
        """生成洗衣妇信息"""
        townsfolk_players = [p for p in self.players if p["role_type"] == "townsfolk" and p["id"] != player["id"]]
        if not townsfolk_players:
            return {"message": "场上没有其他镇民", "is_drunk_or_poisoned": is_drunk_or_poisoned}
        
        target = random.choice(townsfolk_players)
        other_players = [p for p in self.players if p["id"] not in [player["id"], target["id"]]]
        decoy = random.choice(other_players) if other_players else None
        
        players_shown = [target["name"]]
        if decoy:
            players_shown.append(decoy["name"])
            random.shuffle(players_shown)
        
        return {
            "info_type": "washerwoman",
            "players": players_shown,
            "role": target["role"]["name"],
            "message": f"在 {' 和 '.join(players_shown)} 中，有一人是 {target['role']['name']}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_librarian_info(self, player, is_drunk_or_poisoned=False):
        """生成图书管理员信息"""
        outsider_players = [p for p in self.players if p["role_type"] == "outsider"]
        if not outsider_players:
            return {"message": "场上没有外来者（你得知0个玩家是外来者）", "is_drunk_or_poisoned": is_drunk_or_poisoned}
        
        target = random.choice(outsider_players)
        other_players = [p for p in self.players if p["id"] not in [player["id"], target["id"]]]
        decoy = random.choice(other_players) if other_players else None
        
        players_shown = [target["name"]]
        if decoy:
            players_shown.append(decoy["name"])
            random.shuffle(players_shown)
        
        # 获取目标的真实角色名（如果是酒鬼，显示"酒鬼"而不是假身份）
        if target.get("is_the_drunk") and target.get("true_role"):
            role_name = target["true_role"]["name"]  # 酒鬼的真实角色名
        else:
            role_name = target["role"]["name"]
        
        return {
            "info_type": "librarian",
            "players": players_shown,
            "role": role_name,
            "message": f"在 {' 和 '.join(players_shown)} 中，有一人是 {role_name}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_investigator_info(self, player, is_drunk_or_poisoned=False):
        """生成调查员信息"""
        # 检查陌客（可能被当作爪牙）
        recluse = next((p for p in self.players if p.get("role") and p["role"].get("id") == "recluse"), None)
        
        minion_players = [p for p in self.players if p["role_type"] == "minion" and self._registers_as_evil_for_good_info(p, "调查员")]
        
        # 如果有陌客，说书人可以选择让陌客被当作爪牙显示
        if recluse and self._is_ability_active(recluse, "被动技能:recluse_investigator") and random.random() < RECLUSE_INVESTIGATOR_MINION_REGISTRATION_RATE:
            target = recluse
            # 随机选择一个爪牙角色来显示
            minion_roles = self.script["roles"].get("minion", [])
            fake_minion_role = random.choice(minion_roles) if minion_roles else {"name": "爪牙"}
            target_role_name = fake_minion_role["name"]
            self.add_log(f"[系统提示] 陌客 {recluse['name']} 被调查员误认为 {target_role_name}", "info")
        elif not minion_players:
            return {"message": "场上没有爪牙", "is_drunk_or_poisoned": is_drunk_or_poisoned}
        else:
            target = random.choice(minion_players)
            target_role_name = target["role"]["name"]
        
        other_players = [p for p in self.players if p["id"] not in [player["id"], target["id"]]]
        decoy = random.choice(other_players) if other_players else None
        
        players_shown = [target["name"]]
        if decoy:
            players_shown.append(decoy["name"])
            random.shuffle(players_shown)
        
        return {
            "info_type": "investigator",
            "players": players_shown,
            "role": target_role_name,
            "message": f"在 {' 和 '.join(players_shown)} 中，有一人是 {target_role_name}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_chef_info(self, player, is_drunk_or_poisoned=False):
        return generate_chef_info(self, player, is_drunk_or_poisoned)
    
    def _generate_empath_info(self, player, is_drunk_or_poisoned=False):
        return generate_empath_info(
            self,
            player,
            is_drunk_or_poisoned,
            RECLUSE_EMPATH_EVIL_REGISTRATION_RATE,
            random
        )
    
    def _generate_fortune_teller_info(self, player, target_players, is_drunk_or_poisoned=False):
        return generate_fortune_teller_info(
            self,
            player,
            target_players,
            is_drunk_or_poisoned,
            RECLUSE_FORTUNE_TELLER_DEMON_REGISTRATION_RATE,
            random
        )
    
    def _generate_clockmaker_info(self, player, is_drunk_or_poisoned=False):
        """生成钟表匠信息"""
        demon_player = next((p for p in self.players if p["role_type"] == "demon"), None)
        minion_players = [p for p in self.players if self._registers_as_minion_for_good_info(p, "钟表匠")]
        
        if not demon_player or not minion_players:
            return {"message": "无法生成信息", "is_drunk_or_poisoned": is_drunk_or_poisoned}
        
        demon_idx = next((i for i, p in enumerate(self.players) if p["id"] == demon_player["id"]), -1)
        
        min_distance = len(self.players)
        for minion in minion_players:
            minion_idx = next((i for i, p in enumerate(self.players) if p["id"] == minion["id"]), -1)
            # 计算顺时针和逆时针距离
            clockwise = (minion_idx - demon_idx) % len(self.players)
            counter_clockwise = (demon_idx - minion_idx) % len(self.players)
            distance = min(clockwise, counter_clockwise)
            min_distance = min(min_distance, distance)
        
        return {
            "info_type": "clockmaker",
            "distance": min_distance,
            "message": f"恶魔和最近的爪牙之间相隔 {min_distance} 步",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_chambermaid_info(self, player, target_players, is_drunk_or_poisoned=False):
        """生成侍女信息 - 选择两名玩家，得知他们中有多少人今晚因自己的能力而被唤醒"""
        if len(target_players) < 2:
            return {
                "info_type": "chambermaid",
                "message": "请选择两名玩家",
                "is_drunk_or_poisoned": is_drunk_or_poisoned
            }
        
        # 检查目标玩家今晚是否因自己的能力被唤醒
        # 需要根据夜间行动顺序判断
        woke_count = 0
        for target in target_players:
            role = target.get("role")
            if not role:
                continue
            
            # 检查角色是否有夜间能力（first_night 或 other_nights）
            role_has_night_ability = role.get("first_night", True) or role.get("other_nights", True)
            
            # 死亡玩家不会被唤醒（除非有特殊能力）
            if not target["alive"]:
                continue
            
            # 醉酒或中毒的玩家仍然会被唤醒，但能力无效
            # 这里我们计算的是"被唤醒"的数量
            if role_has_night_ability:
                woke_count += 1
        
        target_names = " 和 ".join([t["name"] for t in target_players])
        
        return {
            "info_type": "chambermaid",
            "woke_count": woke_count,
            "message": f"在 {target_names} 中，有 {woke_count} 人今晚因自己的能力而被唤醒",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_seamstress_info(self, player, target_players, is_drunk_or_poisoned=False):
        """生成女裁缝信息 - 选择两名玩家（非自己），得知他们是否属于同一阵营"""
        if len(target_players) < 2:
            return {
                "info_type": "seamstress",
                "message": "请选择两名玩家",
                "is_drunk_or_poisoned": is_drunk_or_poisoned
            }
        
        # 判断两人是否同一阵营
        target1_evil = self._registers_as_evil_for_good_info(target_players[0], "女裁缝")
        target2_evil = self._registers_as_evil_for_good_info(target_players[1], "女裁缝")
        same_team = target1_evil == target2_evil
        
        target_names = " 和 ".join([t["name"] for t in target_players])
        result_text = "是" if same_team else "不是"
        
        return {
            "info_type": "seamstress",
            "same_team": same_team,
            "message": f"{target_names} {result_text}同一阵营",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_dreamer_info(self, player, target_players, is_drunk_or_poisoned=False):
        """生成筑梦师信息 - 选择一名玩家，得知其角色或虚假角色"""
        if not target_players:
            return {
                "info_type": "dreamer",
                "message": "请选择一名玩家",
                "is_drunk_or_poisoned": is_drunk_or_poisoned
            }
        
        target = target_players[0]
        
        # 获取目标的真实角色名（如果是酒鬼，显示"酒鬼"而不是假身份）
        real_role = self._registered_role_name_for_good_info(target, "筑梦师")
        
        # 筑梦师会得知一个正确角色和一个错误角色
        # 这里随机生成一个不同的角色作为干扰项
        all_roles = []
        for role_type in ["townsfolk", "outsider", "minion", "demon"]:
            all_roles.extend([r["name"] for r in self.script["roles"].get(role_type, [])])
        
        fake_roles = [r for r in all_roles if r != real_role]
        fake_role = random.choice(fake_roles) if fake_roles else "无"
        
        # 随机排序两个角色
        roles_shown = [real_role, fake_role]
        random.shuffle(roles_shown)
        
        return {
            "info_type": "dreamer",
            "roles": roles_shown,
            "message": f"{target['name']} 的角色是 {roles_shown[0]} 或 {roles_shown[1]} 其中之一",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_undertaker_info(self, player, is_drunk_or_poisoned=False):
        return generate_undertaker_info(self, player, is_drunk_or_poisoned)
    
    def _generate_ravenkeeper_info(self, player, target_players, is_drunk_or_poisoned=False):
        """生成鸦人保管者信息 - 死亡时选择一名玩家得知其角色"""
        if not target_players:
            return {
                "info_type": "ravenkeeper",
                "message": "请选择一名玩家查看其角色",
                "is_drunk_or_poisoned": is_drunk_or_poisoned
            }
        
        target = target_players[0]
        
        # 获取目标的真实角色名（如果是酒鬼，显示"酒鬼"而不是假身份）
        role_name = self._registered_role_name_for_good_info(target, "守鸦人")
        
        return {
            "info_type": "ravenkeeper",
            "target_role": role_name,
            "message": f"{target['name']} 的角色是 {role_name}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_oracle_info(self, player, is_drunk_or_poisoned=False):
        """生成神谕者信息 - 得知死亡玩家中有几个是邪恶的"""
        dead_players = [p for p in self.players if not p["alive"]]
        evil_dead = sum(1 for p in dead_players if self._registers_as_evil_for_good_info(p, "神谕者"))
        
        return {
            "info_type": "oracle",
            "evil_dead_count": evil_dead,
            "message": f"死亡玩家中有 {evil_dead} 个是邪恶的",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }
    
    def _generate_flowergirl_info(self, player, is_drunk_or_poisoned=False):
        """生成卖花女孩信息 - 得知恶魔昨天是否提名"""
        # 检查最近一天恶魔是否提名
        demon_player = next((p for p in self.players if p["role_type"] == "demon"), None)
        demon_nominated = False
        
        if demon_player and self.nominations:
            # 检查今天的提名记录
            for nom in self.nominations:
                if nom.get("nominator_id") == demon_player["id"]:
                    demon_nominated = True
                    break
        
        result = "提名了" if demon_nominated else "没有提名"
        
        return {
            "info_type": "flowergirl",
            "demon_nominated": demon_nominated,
            "message": f"恶魔昨天{result}",
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }

    def _generate_spy_info(self, player, is_drunk_or_poisoned=False):
        script_roles = []
        for role_group in self.script.get("roles", {}).values():
            for r in role_group:
                if r and r.get("name"):
                    script_roles.append(r["name"])

        grimoire = []
        for p in self.players:
            if is_drunk_or_poisoned and script_roles:
                shown_role = random.choice(script_roles)
            else:
                role_obj = self._get_player_actual_role(p) or {}
                shown_role = role_obj.get("name", "未知")
            grimoire.append(f"{p['name']}：{shown_role}")

        return {
            "info_type": "spy",
            "grimoire": grimoire,
            "message": "魔典信息：\n" + "\n".join(grimoire),
            "is_drunk_or_poisoned": is_drunk_or_poisoned
        }


# 路由
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scripts', methods=['GET'])
def get_scripts():
    """获取所有剧本"""
    scripts_list = []
    for script_id, script in SCRIPTS.items():
        scripts_list.append({
            "id": script_id,
            "name": script["name"],
            "name_en": script["name_en"],
            "description": script["description"]
        })
    return jsonify(scripts_list)

@app.route('/api/script/<script_id>', methods=['GET'])
def get_script_detail(script_id):
    """获取剧本详情"""
    if script_id not in SCRIPTS:
        return jsonify({"error": "剧本不存在"}), 404
    return jsonify(SCRIPTS[script_id])

@app.route('/api/role_distribution/<int:player_count>', methods=['GET'])
def get_distribution(player_count):
    """获取角色分布"""
    distribution = get_role_distribution(player_count)
    return jsonify(distribution)

@app.route('/api/game/create', methods=['POST'])
def create_game():
    """创建新游戏"""
    data = request.json
    script_id = data.get('script_id')
    player_count = data.get('player_count')
    
    if script_id not in SCRIPTS:
        return jsonify({"error": "无效的剧本"}), 400
    
    if not 5 <= player_count <= 16:
        return jsonify({"error": "玩家数量必须在5-16之间"}), 400
    
    game_id = f"game_{len(games) + 1}_{int(datetime.now().timestamp())}"
    game = Game(game_id, script_id, player_count)
    owner_token = secrets.token_urlsafe(24)
    game.owner_token = owner_token
    # 简单的自动清理机制：如果游戏数量超过10个，删除最早创建的
    if len(games) >= 10:
        # 按创建时间排序（假设game_id包含时间戳或按插入顺序）
        # Python 3.7+ 字典保持插入顺序，直接删除第一个key即可
        oldest_game_id = next(iter(games))
        del games[oldest_game_id]

    games[game_id] = game
    
    return jsonify({
        "success": True,
        "game_id": game_id,
        "owner_token": owner_token,
        "game": game.to_dict()
    })

@app.route('/api/game/<game_id>', methods=['GET'])
def get_game(game_id):
    """获取游戏状态"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    return jsonify(games[game_id].to_dict())

@app.route('/api/game/<game_id>/roles', methods=['GET'])
def get_game_roles(game_id):
    """获取游戏可用角色"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    return jsonify(games[game_id].get_available_roles())

@app.route('/api/game/<game_id>/assign_random', methods=['POST'])
def assign_random_roles(game_id):
    """随机分配角色"""
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

@app.route('/api/game/<game_id>/assign_manual', methods=['POST'])
def assign_manual_roles(game_id):
    """手动分配角色"""
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

@app.route('/api/game/<game_id>/start_night', methods=['POST'])
def start_night(game_id):
    """开始夜晚"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    if getattr(game, "mastermind_pending", False):
        resolution_day = getattr(game, "mastermind_resolution_day", None)
        if resolution_day is not None and game.day_number >= resolution_day and not game._had_execution_today():
            game.mastermind_pending = False
            game.mastermind_resolution_day = None
            game.mastermind_forced_winner = "good"
            game.add_log("🧠 主谋结算：延长日无人被处决，善良阵营获胜", "game_end")
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

@app.route('/api/game/<game_id>/night_action', methods=['POST'])
def record_night_action(game_id):
    """记录夜间行动"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    game.record_night_action(
        data.get('player_id'),
        data.get('action'),
        data.get('target'),
        data.get('result'),
        data.get('action_type'),  # 新增: kill, protect, info, skip, drunk 等
        data.get('extra_data')    # 额外数据，如醉酒持续时间
    )
    
    return jsonify({"success": True})

@app.route('/api/game/<game_id>/night_death', methods=['POST'])
def add_night_death(game_id):
    """添加夜间死亡"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    game.add_night_death(data.get('player_id'), data.get('cause', '恶魔击杀'))
    
    return jsonify({"success": True})

# 更新日期: 2026-01-02 - 添加小恶魔传刀和红唇女郎信息返回
@app.route('/api/game/<game_id>/start_day', methods=['POST'])
def start_day(game_id):
    """开始白天"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    game.start_day()
    
    # 检查游戏结束
    game_end_result = game.check_game_end(apply_scarlet_woman=True)
    
    response = {
        "success": True,
        "day_number": game.day_number,
        "night_deaths": game.night_deaths,
        "game_end": game_end_result
    }
    
    # 添加小恶魔传刀信息
    if hasattr(game, 'imp_starpass') and game.imp_starpass:
        response["imp_starpass"] = game.imp_starpass
    
    # 添加红唇女郎触发信息
    if game_end_result.get("scarlet_woman_triggered"):
        response["scarlet_woman_triggered"] = True
        response["new_demon_name"] = game_end_result.get("new_demon")
    
    return jsonify(response)

@app.route('/api/game/<game_id>/nominate', methods=['POST'])
def nominate(game_id):
    """提名"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    result = game.nominate(data.get('nominator_id'), data.get('nominee_id'))
    
    return jsonify(result)

@app.route('/api/game/<game_id>/vote', methods=['POST'])
def vote(game_id):
    """投票"""
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

@app.route('/api/game/<game_id>/execute', methods=['POST'])
# 更新日期: 2026-01-02 - 修复处决后游戏结束检测
def execute(game_id):
    """处决"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    result = game.execute(data.get('nomination_id'))
    
    # 如果 execute 内部没有设置 game_end，则重新检查
    if result.get("success") and "game_end" not in result:
        result["game_end"] = game.check_game_end(apply_scarlet_woman=True)
    
    return jsonify(result)

@app.route('/api/game/<game_id>/player_status', methods=['POST'])
def update_player_status(game_id):
    """更新玩家状态"""
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

@app.route('/api/game/<game_id>/status', methods=['GET'])
def get_game_status(game_id):
    """获取游戏状态"""
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

@app.route('/api/game/<game_id>/set_red_herring', methods=['POST'])
def set_red_herring(game_id):
    """设置占卜师的红鲱鱼"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    target_id = data.get('target_id')
    
    # 找到占卜师
    fortune_teller = next((p for p in game.players if p.get("role") and p["role"].get("id") == "fortune_teller"), None)
    if not fortune_teller:
        return jsonify({"error": "场上没有占卜师"}), 400
    
    # 找到目标玩家
    target = next((p for p in game.players if p["id"] == target_id), None)
    if not target:
        return jsonify({"error": "无效的目标玩家"}), 400
    
    # 检查目标是否是善良阵营
    if target["role_type"] not in ["townsfolk", "outsider"]:
        return jsonify({"error": "红鲱鱼必须是善良玩家"}), 400
    
    fortune_teller["red_herring_id"] = target_id
    game.add_log(f"占卜师的红鲱鱼已设置为 {target['name']}", "setup")
    
    return jsonify({"success": True, "red_herring": target["name"]})

@app.route('/api/game/<game_id>/mayor_substitute', methods=['POST'])
def mayor_substitute(game_id):
    """镇长替死处理"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    substitute_id = data.get('substitute_id')  # 替死的玩家ID，如果为None则镇长自己死
    
    # 找到镇长
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
        
        # 替死玩家死亡，镇长存活
        mayor_target_death["player_id"] = substitute_id
        mayor_target_death["player_name"] = substitute["name"]
        mayor_target_death["cause"] = "镇长替死"
        mayor_target_death.pop("mayor_targeted", None)
        
        game.add_log(f"镇长 {mayor['name']} 的能力触发，{substitute['name']} 替镇长死亡", "night")
        return jsonify({"success": True, "substitute": substitute["name"]})
    else:
        # 镇长自己死亡
        mayor_target_death.pop("mayor_targeted", None)
        
        game.add_log(f"镇长 {mayor['name']} 选择不使用替死能力", "night")
        return jsonify({"success": True, "substitute": None})

# 更新日期: 2026-01-05 - 获取驱魔人之前选过的目标
@app.route('/api/game/<game_id>/exorcist_targets', methods=['GET'])
def get_exorcist_targets(game_id):
    """获取驱魔人之前选过的目标"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    
    previous_targets = getattr(game, 'exorcist_previous_targets', [])
    
    return jsonify({
        "previous_targets": previous_targets
    })

# 更新日期: 2026-01-05 - 获取珀的状态（是否可以杀三人）
@app.route('/api/game/<game_id>/po_status', methods=['GET'])
def get_po_status(game_id):
    """获取珀的能力状态"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    
    # 检查是否有珀
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

# 更新日期: 2026-01-05 - 获取沙巴洛斯可复活的目标列表
@app.route('/api/game/<game_id>/shabaloth_revive_targets', methods=['GET'])
def get_shabaloth_revive_targets(game_id):
    """获取沙巴洛斯可以复活的目标"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    
    # 获取所有死亡的玩家（可以复活）
    dead_players = [{"id": p["id"], "name": p["name"]} for p in game.players if not p["alive"]]
    
    return jsonify({
        "dead_players": dead_players
    })

# 更新日期: 2026-01-05 - 获取恶魔代言人之前选过的目标
@app.route('/api/game/<game_id>/devils_advocate_targets', methods=['GET'])
def get_devils_advocate_targets(game_id):
    """获取恶魔代言人之前选过的目标"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    
    previous_targets = getattr(game, 'devils_advocate_previous_targets', [])
    
    return jsonify({
        "previous_targets": previous_targets
    })

# 更新日期: 2026-01-05 - 和平主义者决定是否让玩家存活
@app.route('/api/game/<game_id>/pacifist_decision', methods=['POST'])
def pacifist_decision(game_id):
    """和平主义者决定是否让善良玩家存活"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    
    nomination_id = data.get('nomination_id')
    player_survives = data.get('survives', False)  # True = 玩家存活, False = 玩家死亡
    
    nomination = next((n for n in game.nominations if n["id"] == nomination_id), None)
    if not nomination:
        return jsonify({"error": "无效的提名"}), 400
    
    nominee = next((p for p in game.players if p["id"] == nomination["nominee_id"]), None)
    if not nominee:
        return jsonify({"error": "无效的被提名者"}), 400
    
    if player_survives:
        # 和平主义者保护玩家存活
        nomination["status"] = "pacifist_saved"
        game.add_log(f"☮️ {nominee['name']} 原本会被处决，但和平主义者的能力使其存活", "execution")
        return jsonify({
            "success": True,
            "executed": False,
            "pacifist_saved": True,
            "player": nominee
        })
    else:
        # 说书人选择让玩家死亡
        nominee["alive"] = False
        nomination["status"] = "executed"
        game.executions.append({
            "day": game.day_number,
            "executed_id": nominee["id"],
            "executed_name": nominee["name"],
            "vote_count": nomination["vote_count"]
        })
        game.add_log(f"{nominee['name']} 被处决（和平主义者未能阻止）", "execution")
        
        # 检查游戏结束
        result = {"success": True, "executed": True, "player": nominee}
        if nominee.get("role_type") == "demon":
            game_end = game.check_game_end(apply_scarlet_woman=True)
            result["game_end"] = game_end
        
        return jsonify(result)

# 更新日期: 2026-01-05 - 月之子选择目标
@app.route('/api/game/<game_id>/moonchild_ability', methods=['POST'])
def moonchild_ability(game_id):
    """月之子选择目标"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    moonchild_id = data.get('moonchild_id')
    target_id = data.get('target_id')
    
    # 找到月之子
    moonchild = next((p for p in game.players if p["id"] == moonchild_id), None)
    if not moonchild:
        return jsonify({"error": "无效的月之子玩家"}), 400
    
    # 检查是否是月之子角色
    if not moonchild.get("role") or moonchild["role"].get("id") != "moonchild":
        return jsonify({"error": "该玩家不是月之子"}), 400
    
    # 清除触发标记
    moonchild["moonchild_triggered"] = False
    game.pending_moonchild = None
    
    # 如果没有选择目标，则放弃能力
    if not target_id:
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择不使用能力", "game_event")
        return jsonify({"success": True, "used": False})
    
    # 找到目标
    target = next((p for p in game.players if p["id"] == target_id), None)
    if not target:
        return jsonify({"error": "无效的目标玩家"}), 400
    
    # 检查目标是否存活
    if not target["alive"]:
        return jsonify({"error": "目标玩家已死亡"}), 400
    
    # 检查目标是否是善良的
    target_is_good = target.get("role_type") in ["townsfolk", "outsider"]
    
    if target_is_good:
        # 善良玩家被选中，死亡
        target["alive"] = False
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择了 {target['name']}（善良玩家），{target['name']} 死亡！", "death")
        
        # 检查游戏结束
        game_end = game.check_game_end(apply_scarlet_woman=True)
        
        return jsonify({
            "success": True,
            "used": True,
            "target_died": True,
            "target_name": target["name"],
            "game_end": game_end
        })
    else:
        # 邪恶玩家被选中，不死亡
        game.add_log(f"🌙 月之子 {moonchild['name']} 选择了 {target['name']}（邪恶玩家），目标存活", "game_event")
        return jsonify({
            "success": True,
            "used": True,
            "target_died": False,
            "target_name": target["name"]
        })

# 更新日期: 2026-01-05 - 检查是否有待处理的月之子
@app.route('/api/game/<game_id>/check_moonchild', methods=['GET'])
def check_moonchild(game_id):
    """检查是否有月之子需要选择目标"""
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

# 更新日期: 2026-01-05 - 处理莽夫被选中的效果
@app.route('/api/game/<game_id>/goon_effect', methods=['POST'])
def goon_effect(game_id):
    """检查并应用莽夫效果"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    data = request.json
    game = games[game_id]
    selector_id = data.get('selector_id')  # 选择莽夫的玩家
    goon_id = data.get('goon_id')  # 莽夫的ID
    
    # 找到莽夫
    goon = next((p for p in game.players if p["id"] == goon_id), None)
    if not goon or goon.get("role", {}).get("id") != "goon":
        return jsonify({"error": "无效的莽夫玩家"}), 400
    
    # 找到选择者
    selector = next((p for p in game.players if p["id"] == selector_id), None)
    if not selector:
        return jsonify({"error": "无效的选择者"}), 400
    
    # 检查莽夫今晚是否已被选择
    if getattr(game, 'goon_chosen_tonight', False):
        return jsonify({
            "success": True,
            "already_chosen": True,
            "message": "莽夫今晚已被其他玩家选择"
        })
    
    # 标记莽夫今晚已被选择
    game.goon_chosen_tonight = True
    
    # 检查莽夫是否醉酒/中毒
    goon_affected = goon.get("drunk") or goon.get("poisoned")
    
    result = {
        "success": True,
        "goon_name": goon["name"],
        "selector_name": selector["name"],
        "already_chosen": False
    }
    
    if not goon_affected:
        # 选择者醉酒到明天黄昏
        selector["drunk"] = True
        selector["drunk_until"] = {
            "day": game.day_number + 1,
            "night": game.night_number + 1
        }
        
        # 莽夫改变阵营为选择者的阵营
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

# 更新日期: 2026-01-08 - 麻脸巫婆获取可变更角色列表
@app.route('/api/game/<game_id>/pit_hag_roles', methods=['GET'])
def get_pit_hag_roles(game_id):
    """获取麻脸巫婆可以选择的角色列表（不在场的角色）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    game = games[game_id]
    
    # 获取当前场上所有角色
    current_role_ids = set()
    for p in game.players:
        if p.get("role"):
            current_role_ids.add(p["role"].get("id"))
    
    # 获取剧本中所有可用角色（不在场的）
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


# ==================== 游戏代码 API ====================

@app.route('/api/game/<game_id>/code', methods=['GET'])
def get_game_code(game_id):
    """获取游戏代码（用于玩家加入）"""
    if game_id not in games:
        return jsonify({"error": "游戏不存在"}), 404
    
    # 返回游戏ID的简短版本作为代码
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


if __name__ == '__main__':
    app.run(debug=True, port=5000)
