import unittest
import random
from unittest.mock import patch, MagicMock

from main import app, games
from config.balance import POISON_FAILURE_RATE
from services.info_generators import (
    generate_chef_info, generate_empath_info, generate_fortune_teller_info
)


class TestPoisonerEffect(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        games.clear()

    def _create_game_with_roles(self, assignments, script_id="trouble_brewing", player_count=5):
        create_resp = self.client.post(
            "/api/game/create",
            json={"script_id": script_id, "player_count": player_count},
        )
        self.assertEqual(create_resp.status_code, 200)
        game_id = create_resp.get_json()["game_id"]
        assign_resp = self.client.post(
            f"/api/game/{game_id}/assign_manual",
            json={"assignments": assignments},
        )
        self.assertEqual(assign_resp.status_code, 200)
        return game_id

    def _get_game(self, game_id):
        return games.get(game_id)

    def _setup_poison_target(self, game, target_player_id):
        target = next((p for p in game.players if p["id"] == target_player_id), None)
        if target:
            target["poisoned"] = True
            target["poisoned_until"] = {"night": game.night_number + 1, "phase": "night_start"}
        return target

    def _print_separator(self, title):
        print(f"\n{'='*60}")
        print(f"  {title}")
        print('='*60)

    def _print_test_result(self, role_name, test_num, poison_failed, ability_active, detail):
        poison_status = "中毒生效(技能失效)" if not poison_failed else "中毒失效(技能正常)"
        print(f"  [{test_num}/5] {role_name}:")
        print(f"         _roll_poison_failure返回: {poison_failed} ({'中毒失效' if poison_failed else '中毒生效'})")
        print(f"         能力激活: {ability_active}")
        print(f"         详细: {detail}")

    def test_poisoner_vs_washerwoman(self):
        self._print_separator("投毒者 vs 洗衣妇 (Washerwoman)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "洗衣妇", "role_id": "washerwoman"},
                {"name": "洗衣妇目标", "role_id": "librarian"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 2)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "洗衣妇技能")
                
                self._print_test_result("洗衣妇", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_librarian(self):
        self._print_separator("投毒者 vs 图书管理员 (Librarian)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "图书管理员", "role_id": "librarian"},
                {"name": "外来者", "role_id": "drunk"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 2)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "图书管理员技能")
                
                self._print_test_result("图书管理员", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_investigator(self):
        self._print_separator("投毒者 vs 调查员 (Investigator)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "调查员", "role_id": "investigator"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "chef"},
                {"name": "路人B", "role_id": "empath"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "调查员技能")
                
                self._print_test_result("调查员", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_chef(self):
        self._print_separator("投毒者 vs 厨师 (Chef)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "厨师", "role_id": "chef"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "empath"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "厨师技能")
                is_poisoned = not ability_active
                info = generate_chef_info(game, target, is_drunk_or_poisoned=is_poisoned)
                
                self._print_test_result("厨师", i+1, poison_failed, ability_active, 
                    f"信息: {info.get('message', '无信息')}")

    def test_poisoner_vs_empath(self):
        self._print_separator("投毒者 vs 共情者 (Empath)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "共情者", "role_id": "empath"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "共情者技能")
                is_poisoned = not ability_active
                info = generate_empath_info(game, target, is_drunk_or_poisoned=is_poisoned)
                
                self._print_test_result("共情者", i+1, poison_failed, ability_active, 
                    f"信息: {info.get('message', '无信息')}")

    def test_poisoner_vs_fortune_teller(self):
        self._print_separator("投毒者 vs 占卜师 (Fortune Teller)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "占卜师", "role_id": "fortune_teller"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            target_players = [game.players[2], game.players[3]]
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "占卜师技能")
                is_poisoned = not ability_active
                info = generate_fortune_teller_info(game, target, target_players, is_drunk_or_poisoned=is_poisoned)
                
                self._print_test_result("占卜师", i+1, poison_failed, ability_active, 
                    f"信息: {info.get('message', '无信息')}")

    def test_poisoner_vs_undertaker(self):
        self._print_separator("投毒者 vs 送葬者 (Undertaker)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "送葬者", "role_id": "undertaker"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            game.executions.append({
                "day": 1,
                "executed_id": 4,
                "executed_name": "路人B",
                "executed_role": "厨师",
                "reason": "vote"
            })
            game.players[4]["alive"] = False
            
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "送葬者技能")
                
                self._print_test_result("送葬者", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_monk(self):
        self._print_separator("投毒者 vs 僧侣 (Monk)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "僧侣", "role_id": "monk"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "保护目标", "role_id": "washerwoman"},
                {"name": "路人", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "僧侣技能")
                
                self._print_test_result("僧侣", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_ravenkeeper(self):
        self._print_separator("投毒者 vs 守鸦人 (Ravenkeeper)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "守鸦人", "role_id": "ravenkeeper"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            target["alive"] = False
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "守鸦人技能")
                
                self._print_test_result("守鸦人", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_virgin(self):
        self._print_separator("投毒者 vs 贞洁者 (Virgin)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "贞洁者", "role_id": "virgin"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "提名者", "role_id": "washerwoman"},
                {"name": "路人", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "贞洁者技能")
                
                self._print_test_result("贞洁者", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_slayer(self):
        self._print_separator("投毒者 vs 杀手 (Slayer)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "杀手", "role_id": "slayer"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            game.current_phase = "day"
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "杀手技能")
                
                self._print_test_result("杀手", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_soldier(self):
        self._print_separator("投毒者 vs 士兵 (Soldier)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "士兵", "role_id": "soldier"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "士兵技能")
                
                self._print_test_result("士兵", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_vs_mayor(self):
        self._print_separator("投毒者 vs 镇长 (Mayor)")
        
        for i in range(5):
            games.clear()
            game_id = self._create_game_with_roles([
                {"name": "镇长", "role_id": "mayor"},
                {"name": "投毒者", "role_id": "poisoner"},
                {"name": "小恶魔", "role_id": "imp"},
                {"name": "路人A", "role_id": "washerwoman"},
                {"name": "路人B", "role_id": "chef"},
            ])
            game = self._get_game(game_id)
            target = self._setup_poison_target(game, 1)
            
            poison_failed = (i >= 3)
            with patch.object(game, '_roll_poison_failure', return_value=poison_failed):
                ability_active = game._is_ability_active(target, "镇长技能")
                
                self._print_test_result("镇长", i+1, poison_failed, ability_active, 
                    f"能力状态: {'正常' if ability_active else '失效'}")

    def test_poisoner_summary(self):
        self._print_separator("投毒者测试总结")
        print(f"\n  POISON_FAILURE_RATE = {POISON_FAILURE_RATE}")
        print(f"\n  源码逻辑 (main.py#L1744-1749):")
        print(f"  ```python")
        print(f"  def _roll_poison_failure(self, player, scene=''):")
        print(f"      if not player or not player.get('poisoned', False):")
        print(f"          return False")
        print(f"      failed = random.random() < POISON_FAILURE_RATE")
        print(f"      self.add_log(f'[系统] {{player['name']}} 中毒判定：{{'失效' if failed else '生效'}}（{{scene or '技能'}}）', 'info')")
        print(f"      return failed")
        print(f"  ```")
        print(f"\n  返回值含义:")
        print(f"  - True  = 中毒失效 -> 技能正常 (random.random() < {POISON_FAILURE_RATE}, 概率{POISON_FAILURE_RATE*100:.0f}%)")
        print(f"  - False = 中毒生效 -> 技能失效 (random.random() >= {POISON_FAILURE_RATE}, 概率{(1-POISON_FAILURE_RATE)*100:.0f}%)")
        print(f"\n  测试说明:")
        print(f"  - 前3次测试: _roll_poison_failure 返回 False (中毒生效, 技能失效)")
        print(f"  - 后2次测试: _roll_poison_failure 返回 True (中毒失效, 技能正常)")
        print(f"\n")


if __name__ == '__main__':
    unittest.main(verbosity=2)
