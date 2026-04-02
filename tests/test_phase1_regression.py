import unittest
import time
from unittest.mock import patch

from main import app, games


class TestPhase1Regression(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        games.clear()

    def _join_player(self, game_id, player_id):
        join_resp = self.client.post(
            "/api/player/join_game",
            json={"game_id": game_id, "player_id": player_id},
        )
        self.assertEqual(join_resp.status_code, 200)
        return join_resp.get_json()["reconnect_token"]

    def _create_game_with_manual_roles(self, assignments, player_count=5):
        create_resp = self.client.post(
            "/api/game/create",
            json={"script_id": "trouble_brewing", "player_count": player_count},
        )
        self.assertEqual(create_resp.status_code, 200)
        game_id = create_resp.get_json()["game_id"]
        assign_resp = self.client.post(
            f"/api/game/{game_id}/assign_manual",
            json={"assignments": assignments},
        )
        self.assertEqual(assign_resp.status_code, 200)
        return game_id

    def _create_bmr_game_with_manual_roles(self, assignments, player_count=5):
        create_resp = self.client.post(
            "/api/game/create",
            json={"script_id": "bad_moon_rising", "player_count": player_count},
        )
        self.assertEqual(create_resp.status_code, 200)
        game_id = create_resp.get_json()["game_id"]
        assign_resp = self.client.post(
            f"/api/game/{game_id}/assign_manual",
            json={"assignments": assignments},
        )
        self.assertEqual(assign_resp.status_code, 200)
        return game_id

    def _imp_self_kill_to_start_day(self, game_id):
        start_night_resp = self.client.post(f"/api/game/{game_id}/start_night")
        self.assertEqual(start_night_resp.status_code, 200)
        night_action_resp = self.client.post(
            f"/api/game/{game_id}/night_action",
            json={
                "player_id": 3,
                "action": "小恶魔击杀",
                "target": 3,
                "result": "自杀传刀",
                "action_type": "kill",
            },
        )
        self.assertEqual(night_action_resp.status_code, 200)
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        return start_day_resp.get_json()

    def test_positive_drunk_with_chef_info_nominates_virgin_should_enter_voting(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "drunk", "drunk_fake_role_id": "chef"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        drunk_player = next(p for p in game.players if p["id"] == 1)
        self.assertEqual(drunk_player.get("role", {}).get("id"), "chef")
        self.assertEqual((drunk_player.get("true_role") or {}).get("id"), "drunk")
        self.client.post(f"/api/game/{game_id}/start_day")
        reconnect_token = self._join_player(game_id, 1)
        nominate_resp = self.client.post(
            "/api/player/nominate",
            json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2, "reconnect_token": reconnect_token},
        )
        nominate_data = nominate_resp.get_json()
        self.assertEqual(nominate_resp.status_code, 200)
        self.assertTrue(nominate_data.get("success"))
        self.assertFalse(nominate_data.get("virgin_triggered"))
        self.assertEqual(nominate_data.get("nomination", {}).get("status"), "voting")
        self.assertEqual(game.current_phase, "day")
        self.assertTrue(next(p for p in game.players if p["id"] == 1).get("alive"))

    def test_negative_townsfolk_nominates_virgin_should_trigger_and_not_enter_voting(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        reconnect_token = self._join_player(game_id, 1)
        nominate_resp = self.client.post(
            "/api/player/nominate",
            json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2, "reconnect_token": reconnect_token},
        )
        nominate_data = nominate_resp.get_json()
        self.assertEqual(nominate_resp.status_code, 200)
        self.assertTrue(nominate_data.get("success"))
        self.assertTrue(nominate_data.get("virgin_triggered"))
        self.assertNotEqual(nominate_data.get("nomination", {}).get("status"), "voting")
        self.assertFalse(next(p for p in game.players if p["id"] == 1).get("alive"))

    def test_positive_imp_self_kill_should_die_and_poisoner_becomes_new_imp(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        day_data = self._imp_self_kill_to_start_day(game_id)
        self.assertIn("imp_starpass", day_data)
        self.assertTrue(day_data["imp_starpass"])
        starpass = day_data["imp_starpass"][-1]
        self.assertEqual(starpass.get("old_imp_id"), 3)
        self.assertEqual(starpass.get("new_imp_id"), 4)

        game = games[game_id]
        old_imp = next(p for p in game.players if p["id"] == 3)
        new_imp = next(p for p in game.players if p["id"] == 4)

        self.assertFalse(old_imp.get("alive"))
        self.assertTrue(new_imp.get("alive"))
        self.assertEqual(new_imp.get("role", {}).get("id"), "imp")
        self.assertEqual(new_imp.get("role_type"), "demon")

    def test_negative_imp_self_kill_without_alive_minion_should_not_starpass(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        poisoner = next(p for p in game.players if p["id"] == 4)
        poisoner["alive"] = False
        day_data = self._imp_self_kill_to_start_day(game_id)
        self.assertFalse(day_data.get("imp_starpass"))
        old_imp = next(p for p in game.players if p["id"] == 3)
        self.assertFalse(old_imp.get("alive"))
        self.assertEqual(poisoner.get("role", {}).get("id"), "poisoner")
        self.assertEqual(poisoner.get("role_type"), "minion")

    def test_demon_kill_dead_target_should_peaceful_night_with_no_new_death(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        target = next(p for p in game.players if p["id"] == 2)
        target["alive"] = False
        dead_count_before = sum(1 for p in game.players if not p.get("alive", True))

        start_night_resp = self.client.post(f"/api/game/{game_id}/start_night")
        self.assertEqual(start_night_resp.status_code, 200)
        night_action_resp = self.client.post(
            f"/api/game/{game_id}/night_action",
            json={
                "player_id": 3,
                "action": "小恶魔击杀",
                "target": 2,
                "result": "击杀已死亡目标",
                "action_type": "kill",
            },
        )
        self.assertEqual(night_action_resp.status_code, 200)

        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()

        self.assertEqual(day_data.get("night_deaths"), [])
        self.assertEqual(sum(1 for p in game.players if not p.get("alive", True)), dead_count_before)

    def test_recluse_fortune_teller_registers_as_demon(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "fortune_teller"},
                {"name": "2号", "role_id": "recluse"},
                {"name": "3号", "role_id": "chef"},
                {"name": "4号", "role_id": "imp"},
                {"name": "5号", "role_id": "poisoner"},
            ]
        )
        game = games[game_id]
        fortune_teller = next(p for p in game.players if p["id"] == 1)
        fortune_teller["red_herring_id"] = None
        with patch("main.random.random", return_value=0.0):
            resp = self.client.post(
                f"/api/game/{game_id}/generate_info",
                json={"player_id": 1, "info_type": "fortune_teller", "targets": [2, 3]},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("info_type"), "fortune_teller")
        self.assertTrue(data.get("has_demon"))

    def test_slayer_uses_ability_on_recluse_and_recluse_dies(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "slayer"},
                {"name": "2号", "role_id": "recluse"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        self.client.post(f"/api/game/{game_id}/start_day")
        with patch("main.random.random", return_value=0.0):
            resp = self.client.post(
                f"/api/game/{game_id}/slayer_ability",
                json={"slayer_id": 1, "target_id": 2},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("target_died"))
        game = games[game_id]
        self.assertFalse(next(p for p in game.players if p["id"] == 2).get("alive"))

    def test_recluse_executed_undertaker_gets_imp_info(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "undertaker"},
                {"name": "2号", "role_id": "recluse"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        executed = next(p for p in game.players if p["id"] == 2)
        executed["alive"] = False
        game.executions.append(
            {
                "day": game.day_number,
                "executed_id": 2,
                "executed_name": executed["name"],
                "reason": "vote",
                "vote_count": 3,
                "required_votes": 3,
            }
        )
        self.client.post(f"/api/game/{game_id}/start_night")
        with patch("main.random.random", return_value=0.0):
            resp = self.client.post(
                f"/api/game/{game_id}/generate_info",
                json={"player_id": 1, "info_type": "undertaker"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("info_type"), "undertaker")
        self.assertEqual(data.get("executed_role"), "小恶魔")

    def test_special_case_chef_shows_zero_when_recluse_between_imp_and_evil_traveler(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "imp"},
                {"name": "3号", "role_id": "recluse"},
                {"name": "4号", "role_id": "washerwoman"},
                {"name": "5号", "role_id": "librarian"},
            ]
        )
        game = games[game_id]
        traveler = next(p for p in game.players if p["id"] == 4)
        traveler["role_type"] = "traveler"
        traveler["traveler_alignment"] = "evil"
        with patch("main.random.random", return_value=0.0):
            resp = self.client.post(
                f"/api/game/{game_id}/generate_info",
                json={"player_id": 1, "info_type": "chef"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("info_type"), "chef")
        self.assertEqual(data.get("pairs"), 0)

    def test_fortune_teller_red_herring_good_player_should_trigger_has_demon(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "fortune_teller"},
                {"name": "2号", "role_id": "chef"},
                {"name": "3号", "role_id": "washerwoman"},
                {"name": "4号", "role_id": "imp"},
                {"name": "5号", "role_id": "poisoner"},
            ]
        )
        set_resp = self.client.post(
            f"/api/game/{game_id}/set_red_herring",
            json={"target_id": 2},
        )
        self.assertEqual(set_resp.status_code, 200)
        info_resp = self.client.post(
            f"/api/game/{game_id}/generate_info",
            json={"player_id": 1, "info_type": "fortune_teller", "targets": [2, 3]},
        )
        self.assertEqual(info_resp.status_code, 200)
        info_data = info_resp.get_json()
        self.assertEqual(info_data.get("info_type"), "fortune_teller")
        self.assertTrue(info_data.get("has_demon"))

    def test_fortune_teller_without_red_herring_and_without_demon_should_be_false(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "fortune_teller"},
                {"name": "2号", "role_id": "chef"},
                {"name": "3号", "role_id": "washerwoman"},
                {"name": "4号", "role_id": "imp"},
                {"name": "5号", "role_id": "poisoner"},
            ]
        )
        game = games[game_id]
        fortune_teller = next(p for p in game.players if p["id"] == 1)
        fortune_teller["red_herring_id"] = None
        info_resp = self.client.post(
            f"/api/game/{game_id}/generate_info",
            json={"player_id": 1, "info_type": "fortune_teller", "targets": [2, 3]},
        )
        self.assertEqual(info_resp.status_code, 200)
        info_data = info_resp.get_json()
        self.assertEqual(info_data.get("info_type"), "fortune_teller")
        self.assertFalse(info_data.get("has_demon"))

    def test_mayor_substitute_rate_hit_should_kill_substitute_not_mayor(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "mayor"},
                {"name": "2号", "role_id": "chef"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 3, "action": "小恶魔击杀", "target": 1, "action_type": "kill"},
        )
        with patch("main.random.random", return_value=0.0):
            start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertTrue(any(d.get("cause") == "镇长替死" for d in day_data.get("night_deaths", [])))
        mayor = next(p for p in game.players if p["id"] == 1)
        self.assertTrue(mayor.get("alive"))

    def test_mayor_substitute_rate_miss_should_kill_mayor(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "mayor"},
                {"name": "2号", "role_id": "chef"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 3, "action": "小恶魔击杀", "target": 1, "action_type": "kill"},
        )
        with patch("main.random.random", return_value=0.99):
            start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertFalse(any(d.get("cause") == "镇长替死" for d in day_data.get("night_deaths", [])))
        mayor = next(p for p in game.players if p["id"] == 1)
        self.assertFalse(mayor.get("alive"))

    def test_spy_nominates_virgin_should_trigger_when_registers_as_townsfolk(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "spy"},
                {"name": "2号", "role_id": "virgin"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "washerwoman"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        reconnect_token = self._join_player(game_id, 1)
        with patch("main.random.random", return_value=0.0):
            nominate_resp = self.client.post(
                "/api/player/nominate",
                json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2, "reconnect_token": reconnect_token},
            )
        self.assertEqual(nominate_resp.status_code, 200)
        nominate_data = nominate_resp.get_json()
        self.assertTrue(nominate_data.get("virgin_triggered"))
        self.assertFalse(next(p for p in game.players if p["id"] == 1).get("alive"))

    def test_bmr_exorcist_cannot_choose_same_target_twice_in_row(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "exorcist"},
                {"name": "2号", "role_id": "zombuul"},
                {"name": "3号", "role_id": "innkeeper"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        game.exorcist_previous_targets = [2]
        reconnect_token = self._join_player(game_id, 1)
        resp = self.client.post(
            "/api/player/night_action",
            json={
                "game_id": game_id,
                "player_id": 1,
                "targets": [2],
                "action_type": "exorcist",
                "extra_data": {},
                "reconnect_token": reconnect_token,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不能连续两晚选择同一名玩家", (resp.get_json() or {}).get("error", ""))

    def test_bmr_devils_advocate_cannot_choose_same_target_twice_in_row(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "devils_advocate"},
                {"name": "2号", "role_id": "zombuul"},
                {"name": "3号", "role_id": "innkeeper"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        game.devils_advocate_previous_targets = [2]
        reconnect_token = self._join_player(game_id, 1)
        resp = self.client.post(
            "/api/player/night_action",
            json={
                "game_id": game_id,
                "player_id": 1,
                "targets": [2],
                "action_type": "devils_advocate",
                "extra_data": {},
                "reconnect_token": reconnect_token,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不能连续两晚选择同一名玩家", (resp.get_json() or {}).get("error", ""))

    def test_bmr_assassin_kill_ignores_protection(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "assassin"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        target = next(p for p in game.players if p["id"] == 1)
        target["protected"] = True
        game.protected_players.append(1)
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 1, "action": "刺客击杀", "target": 1, "action_type": "assassin_kill"},
        )
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertTrue(any(d.get("cause") == "刺客击杀" and d.get("player_id") == 1 for d in day_data.get("night_deaths", [])))
        self.assertFalse(next(p for p in game.players if p["id"] == 1).get("alive"))

    def test_bmr_grandmother_dies_when_demon_kills_grandchild(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "grandmother"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        grandmother = next(p for p in game.players if p["id"] == 1)
        grandchild = next(p for p in game.players if p["id"] == 2)
        grandmother["grandchild_id"] = 2
        grandchild["grandchild_of"] = 1
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 3, "action": "僵怖击杀", "target": 2, "action_type": "zombuul_kill"},
        )
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertTrue(any(d.get("player_id") == 2 for d in day_data.get("night_deaths", [])))
        self.assertTrue(any(d.get("cause") == "祖母殉孙" and d.get("player_id") == 1 for d in day_data.get("night_deaths", [])))

    def test_bmr_gambler_wrong_guess_should_die_at_night(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "gambler"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "goon"},
                {"name": "5号", "role_id": "exorcist"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={
                "player_id": 1,
                "action": "赌徒猜测",
                "target": 2,
                "action_type": "gambler_guess",
                "extra_data": {"guessed_role_id": "zombuul"},
            },
        )
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertTrue(any(d.get("player_id") == 1 and d.get("cause") == "赌徒猜错" for d in day_data.get("night_deaths", [])))
        self.assertFalse(next(p for p in game.players if p["id"] == 1).get("alive"))

    def test_bmr_gossip_kill_should_respect_protection(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "gossip"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "goon"},
                {"name": "5号", "role_id": "exorcist"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        protected_target = next(p for p in game.players if p["id"] == 4)
        protected_target["protected"] = True
        game.protected_players.append(4)
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 1, "action": "造谣者触发", "target": 4, "action_type": "gossip_kill"},
        )
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        day_data = start_day_resp.get_json()
        self.assertFalse(any(d.get("player_id") == 4 for d in day_data.get("night_deaths", [])))
        self.assertTrue(next(p for p in game.players if p["id"] == 4).get("alive"))

    def test_bmr_mastermind_should_trigger_after_demon_execution(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "mastermind"},
                {"name": "2号", "role_id": "po"},
                {"name": "3号", "role_id": "innkeeper"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        nominate_resp = self.client.post(
            f"/api/game/{game_id}/nominate",
            json={"nominator_id": 3, "nominee_id": 2},
        )
        nomination_id = nominate_resp.get_json().get("nomination", {}).get("id")
        for voter_id in [1, 3, 4]:
            self.client.post(
                f"/api/game/{game_id}/vote",
                json={"nomination_id": nomination_id, "voter_id": voter_id, "vote": True},
            )
        execute_resp = self.client.post(
            f"/api/game/{game_id}/execute",
            json={"nomination_id": nomination_id},
        )
        self.assertEqual(execute_resp.status_code, 200)
        data = execute_resp.get_json()
        self.assertTrue(data.get("mastermind_triggered"))
        self.assertFalse(data.get("game_end", {}).get("ended"))
        self.assertTrue(game.mastermind_pending)
        self.assertEqual(game.mastermind_resolution_day, game.day_number + 1)

    def test_bmr_mastermind_resolution_day_execution_should_flip_loser_team(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "mastermind"},
                {"name": "2号", "role_id": "po"},
                {"name": "3号", "role_id": "innkeeper"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        nominate_resp = self.client.post(
            f"/api/game/{game_id}/nominate",
            json={"nominator_id": 3, "nominee_id": 2},
        )
        nomination_id = nominate_resp.get_json().get("nomination", {}).get("id")
        for voter_id in [1, 3, 4]:
            self.client.post(
                f"/api/game/{game_id}/vote",
                json={"nomination_id": nomination_id, "voter_id": voter_id, "vote": True},
            )
        self.client.post(f"/api/game/{game_id}/execute", json={"nomination_id": nomination_id})
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(f"/api/game/{game_id}/start_day")
        nominate_resp2 = self.client.post(
            f"/api/game/{game_id}/nominate",
            json={"nominator_id": 4, "nominee_id": 3},
        )
        nomination_id2 = nominate_resp2.get_json().get("nomination", {}).get("id")
        for voter_id in [1, 4, 5]:
            self.client.post(
                f"/api/game/{game_id}/vote",
                json={"nomination_id": nomination_id2, "voter_id": voter_id, "vote": True},
            )
        execute_resp2 = self.client.post(
            f"/api/game/{game_id}/execute",
            json={"nomination_id": nomination_id2},
        )
        self.assertEqual(execute_resp2.status_code, 200)
        end_data = execute_resp2.get_json().get("game_end", {})
        self.assertTrue(end_data.get("ended"))
        self.assertEqual(end_data.get("winner"), "evil")

    def test_bmr_mastermind_resolution_day_without_execution_should_good_win(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "mastermind"},
                {"name": "2号", "role_id": "po"},
                {"name": "3号", "role_id": "innkeeper"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        nominate_resp = self.client.post(
            f"/api/game/{game_id}/nominate",
            json={"nominator_id": 3, "nominee_id": 2},
        )
        nomination_id = nominate_resp.get_json().get("nomination", {}).get("id")
        for voter_id in [1, 3, 4]:
            self.client.post(
                f"/api/game/{game_id}/vote",
                json={"nomination_id": nomination_id, "voter_id": voter_id, "vote": True},
            )
        self.client.post(f"/api/game/{game_id}/execute", json={"nomination_id": nomination_id})
        self.assertTrue(game.mastermind_pending)
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(f"/api/game/{game_id}/start_day")
        start_night_resp = self.client.post(f"/api/game/{game_id}/start_night")
        self.assertEqual(start_night_resp.status_code, 200)
        end_data = start_night_resp.get_json().get("game_end", {})
        self.assertTrue(end_data.get("ended"))
        self.assertEqual(end_data.get("winner"), "good")

    def test_bmr_minstrel_should_make_others_drunk_until_next_dusk(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "minstrel"},
                {"name": "2号", "role_id": "devils_advocate"},
                {"name": "3号", "role_id": "po"},
                {"name": "4号", "role_id": "innkeeper"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_day")
        nominate_resp = self.client.post(
            f"/api/game/{game_id}/nominate",
            json={"nominator_id": 4, "nominee_id": 2},
        )
        nomination_id = nominate_resp.get_json().get("nomination", {}).get("id")
        for voter_id in [1, 3, 4]:
            self.client.post(
                f"/api/game/{game_id}/vote",
                json={"nomination_id": nomination_id, "voter_id": voter_id, "vote": True},
            )
        execute_resp = self.client.post(
            f"/api/game/{game_id}/execute",
            json={"nomination_id": nomination_id},
        )
        self.assertEqual(execute_resp.status_code, 200)
        self.assertEqual(game.minstrel_effect_until_day, game.day_number + 1)
        minstrel = next(p for p in game.players if p["id"] == 1)
        innkeeper = next(p for p in game.players if p["id"] == 4)
        self.assertTrue(game._is_ability_active(minstrel, "测试"))
        self.assertFalse(game._is_ability_active(innkeeper, "测试"))
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(f"/api/game/{game_id}/start_day")
        self.assertIsNotNone(game.minstrel_effect_until_day)
        self.client.post(f"/api/game/{game_id}/start_night")
        self.assertIsNone(game.minstrel_effect_until_day)

    def test_bmr_courtier_can_make_demon_drunk_and_block_kill(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "courtier"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 1, "action": "侍臣灌醉", "target": 3, "action_type": "drunk"},
        )
        with patch("main.random.random", return_value=0.99):
            self.client.post(
                f"/api/game/{game_id}/night_action",
                json={"player_id": 3, "action": "僵怖击杀", "target": 2, "action_type": "zombuul_kill"},
            )
        self.client.post(f"/api/game/{game_id}/start_day")
        self.assertTrue(next(p for p in game.players if p["id"] == 2).get("alive"))
        self.assertTrue(next(p for p in game.players if p["id"] == 3).get("drunk"))
        self.assertTrue(next(p for p in game.players if p["id"] == 1).get("ability_used"))

    def test_bmr_professor_can_revive_dead_player(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "professor"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        dead_player = next(p for p in game.players if p["id"] == 4)
        dead_player["alive"] = False
        dead_player["vote_token"] = False
        self.client.post(f"/api/game/{game_id}/start_night")
        resp = self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 1, "action": "教授复活", "target": 4, "action_type": "revive"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(dead_player.get("alive"))
        self.assertTrue(dead_player.get("vote_token"))
        self.assertTrue(next(p for p in game.players if p["id"] == 1).get("ability_used"))

    def test_bmr_moonchild_day_action_can_kill_same_alignment_target(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "moonchild"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        moonchild = next(p for p in game.players if p["id"] == 1)
        moonchild["alive"] = False
        moonchild["moonchild_triggered"] = True
        game.current_phase = "day"
        game.pending_moonchild = 1

        reconnect_token = self._join_player(game_id, 1)
        action_resp = self.client.get(
            f"/api/player/day_action/{game_id}/1?reconnect_token={reconnect_token}"
        )
        self.assertEqual(action_resp.status_code, 200)
        self.assertTrue(action_resp.get_json().get("has_pending"))

        submit_resp = self.client.post(
            "/api/player/submit_action",
            json={"game_id": game_id, "player_id": 1, "targets": [2], "reconnect_token": reconnect_token},
        )
        self.assertEqual(submit_resp.status_code, 200)
        self.assertFalse(next(p for p in game.players if p["id"] == 2).get("alive"))
        self.assertIsNone(game.pending_moonchild)

    def test_bmr_sailor_cannot_die_from_night_kill_while_sober(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "sailor"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        game = games[game_id]
        self.client.post(f"/api/game/{game_id}/start_night")
        self.client.post(
            f"/api/game/{game_id}/night_action",
            json={"player_id": 3, "action": "僵怖击杀", "target": 1, "action_type": "zombuul_kill"},
        )
        start_day_resp = self.client.post(f"/api/game/{game_id}/start_day")
        self.assertEqual(start_day_resp.status_code, 200)
        self.assertTrue(next(p for p in game.players if p["id"] == 1).get("alive"))
        self.assertFalse(any(d.get("player_id") == 1 for d in start_day_resp.get_json().get("night_deaths", [])))

    def test_bmr_tinker_can_die_on_nomination_roll(self):
        game_id = self._create_bmr_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "tinker"},
                {"name": "2号", "role_id": "innkeeper"},
                {"name": "3号", "role_id": "zombuul"},
                {"name": "4号", "role_id": "gambler"},
                {"name": "5号", "role_id": "goon"},
            ]
        )
        self.client.post(f"/api/game/{game_id}/start_day")
        with patch("main.random.random", return_value=0.0):
            resp = self.client.post(
                f"/api/game/{game_id}/nominate",
                json={"nominator_id": 2, "nominee_id": 3},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(next(p for p in games[game_id].players if p["id"] == 1).get("alive"))

    def test_owner_end_day_when_already_night_should_be_idempotent_success(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "washerwoman"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "soldier"},
            ]
        )
        game = games[game_id]
        game.owner_token = "owner_test_token"
        game.current_phase = "night"
        resp = self.client.post(
            "/api/player/end_day",
            json={"game_id": game_id, "owner_token": "owner_test_token"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("already_transitioned"))
        self.assertEqual(data.get("current_phase"), "night")

    def test_owner_end_day_should_finalize_expired_vote_before_settlement(self):
        game_id = self._create_game_with_manual_roles(
            [
                {"name": "1号", "role_id": "chef"},
                {"name": "2号", "role_id": "washerwoman"},
                {"name": "3号", "role_id": "imp"},
                {"name": "4号", "role_id": "poisoner"},
                {"name": "5号", "role_id": "soldier"},
            ]
        )
        game = games[game_id]
        game.owner_token = "owner_test_token"
        self.client.post(f"/api/game/{game_id}/start_day")
        reconnect_token = self._join_player(game_id, 4)
        nominate_resp = self.client.post(
            "/api/player/nominate",
            json={"game_id": game_id, "nominator_id": 4, "nominee_id": 1, "reconnect_token": reconnect_token},
        )
        self.assertEqual(nominate_resp.status_code, 200)
        nomination = game.nominations[-1]
        nomination["vote_count"] = 3
        nomination["vote_deadline_at"] = time.time() - 1
        resp = self.client.post(
            "/api/player/end_day",
            json={"game_id": game_id, "owner_token": "owner_test_token"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(game.current_phase, "night")
        executed_player = next(p for p in game.players if p["id"] == 1)
        self.assertFalse(executed_player.get("alive"))
        self.assertTrue(data.get("execution_result", {}).get("executed"))


if __name__ == "__main__":
    unittest.main()
