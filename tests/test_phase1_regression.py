import unittest
from unittest.mock import patch

from main import app, games


class TestPhase1Regression(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        games.clear()

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
        nominate_resp = self.client.post(
            "/api/player/nominate",
            json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2},
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
        nominate_resp = self.client.post(
            "/api/player/nominate",
            json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2},
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
        with patch("main.random.random", return_value=0.0):
            nominate_resp = self.client.post(
                "/api/player/nominate",
                json={"game_id": game_id, "nominator_id": 1, "nominee_id": 2},
            )
        self.assertEqual(nominate_resp.status_code, 200)
        nominate_data = nominate_resp.get_json()
        self.assertTrue(nominate_data.get("virgin_triggered"))
        self.assertFalse(next(p for p in game.players if p["id"] == 1).get("alive"))


if __name__ == "__main__":
    unittest.main()
