import random
from collections import Counter
from itertools import count

import pytest

from matchday.domain.draw import Draw, DrawConflict, DrawError, DrawPlayer, draw_teams, team_sizes

IDS = count(1)


def players(*potes: int) -> list[DrawPlayer]:
    return [DrawPlayer(next(IDS), pote) for pote in potes]


def spread(potes: dict[int, int]) -> list[DrawPlayer]:
    """{pote: how many players} -> players."""
    return players(*(pote for pote, n in potes.items() for _ in range(n)))


def pote_counts(draw: Draw, pool: list[DrawPlayer]) -> list[Counter]:
    pote_of = {p.player_id: p.pote for p in pool}
    return [Counter(pote_of[pid] for pid in team) for team in draw.teams]


def everyone(draw: Draw) -> list[int]:
    return [pid for team in draw.teams for pid in team] + list(draw.in_goal)


def gap(draw: Draw, pool: list[DrawPlayer]) -> int:
    """Highest minus lowest pote sum of a team."""
    pote_of = {p.player_id: p.pote for p in pool}
    sums = [sum(pote_of[pid] for pid in team) for team in draw.teams]
    return max(sums) - min(sums)


def best_gap(
    pool: list[DrawPlayer], groups: list[list[int]], team_count: int, team_size: int
) -> int:
    """The smallest gap any way of dealing `pool` can have, by trying them all."""
    inside = {pid for group in groups for pid in group}
    units = [list(group) for group in groups] + [
        [p.player_id] for p in pool if p.player_id not in inside
    ]
    pote_of = {p.player_id: p.pote for p in pool}
    best = 10**6

    def place(k: int, sums: list[int], room: list[int]) -> None:
        nonlocal best
        if k == len(units):
            best = min(best, max(sums) - min(sums))
            return
        unit = units[k]
        worth = sum(pote_of[pid] for pid in unit)
        for team in range(team_count):
            if room[team] >= len(unit):
                room[team] -= len(unit)
                sums[team] += worth
                place(k + 1, sums, room)
                room[team] += len(unit)
                sums[team] -= worth

    place(0, [0] * team_count, [team_size] * team_count)
    return best


class TestTeamSizes:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (20, (5, 5, 5, 5)),
            (22, (5, 5, 5, 5)),
            (19, (5, 5, 5, 4)),
            (18, (5, 5, 4, 4)),
            (4, (1, 1, 1, 1)),
        ],
    )
    def test_sizes(self, line: int, expected: tuple[int, ...]) -> None:
        assert team_sizes(line) == expected


class TestDraw:
    def test_twenty_line_players_make_four_teams_of_five(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        draw = draw_teams(pool, random.Random(1))
        assert [len(team) for team in draw.teams] == [5, 5, 5, 5]
        assert draw.in_goal == ()
        assert sorted(everyone(draw)) == sorted(p.player_id for p in pool)

    def test_even_potes_give_every_team_one_of_each(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        for seed in range(30):
            counts = pote_counts(draw_teams(pool, random.Random(seed)), pool)
            assert all(c == Counter({1: 1, 2: 1, 3: 1, 4: 1, 5: 1}) for c in counts)

    @pytest.mark.parametrize(("line", "in_goal"), [(21, 1), (22, 2)])
    def test_extra_line_players_are_drawn_to_play_goal(self, line: int, in_goal: int) -> None:
        pool = players(*([3] * line))
        draw = draw_teams(pool, random.Random(7))
        assert len(draw.in_goal) == in_goal
        assert [len(team) for team in draw.teams] == [5, 5, 5, 5]
        assert sorted(everyone(draw)) == sorted(p.player_id for p in pool)

    def test_who_goes_to_goal_is_random(self) -> None:
        pool = players(*([3] * 22))
        picked = {draw_teams(pool, random.Random(seed)).in_goal for seed in range(20)}
        assert len(picked) > 1

    def test_teams_change_from_draw_to_draw(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        results = {
            frozenset(frozenset(team) for team in draw_teams(pool, random.Random(seed)).teams)
            for seed in range(20)
        }
        assert len(results) > 1

    def test_same_seed_same_draw(self) -> None:
        pool = spread({1: 3, 2: 5, 3: 6, 4: 4, 5: 2})
        assert draw_teams(pool, random.Random(42)) == draw_teams(pool, random.Random(42))

    def test_pote_sums_are_as_close_as_possible(self) -> None:
        for seed in range(120):
            rng = random.Random(seed)
            team_count, team_size = rng.choice([(2, 4), (4, 2), (3, 3), (3, 4), (2, 5)])
            pool = players(*(rng.randint(1, 5) for _ in range(team_count * team_size)))
            draw = draw_teams(pool, rng, team_count=team_count, team_size=team_size)
            assert gap(draw, pool) == best_gap(pool, [], team_count, team_size), seed

    def test_pote_sums_are_as_close_as_possible_on_a_real_list(self) -> None:
        # The sums tie (or differ by one, when they cannot divide by four) even when the potes are
        # far from even, which dealing pote by pote could not do.
        for potes in [
            {1: 3, 2: 5, 3: 6, 4: 4, 5: 2},
            {1: 1, 2: 2, 3: 14, 4: 2, 5: 1},
            {1: 2, 2: 7, 3: 3, 4: 5, 5: 3},
            {1: 5, 2: 5, 3: 2, 4: 5, 5: 5},
        ]:
            pool = spread(potes)
            pote_of = {p.player_id: p.pote for p in pool}
            for seed in range(20):
                draw = draw_teams(pool, random.Random(seed))
                on_teams = sum(pote_of[pid] for team in draw.teams for pid in team)
                assert gap(draw, pool) == (on_teams % 4 > 0), seed

    def test_it_is_never_worse_than_the_plain_deal(self) -> None:
        for seed in range(25):
            rng = random.Random(seed)
            pool = players(*(rng.randint(1, 5) for _ in range(rng.choice([18, 19, 20, 21, 22]))))
            plain = draw_teams(pool, random.Random(seed), balance=False)
            best = draw_teams(pool, random.Random(seed))
            assert gap(best, pool) <= gap(plain, pool), seed

    def test_equal_sums_are_decided_by_how_evenly_each_pote_is_spread(self) -> None:
        # (1, 3, 3, 5, 5) and (1, 1, 5, 5, 5) both add up to 17, and so does (3, 3, 3, 3, 5): only
        # the first gives every team a pote 1.
        pool = spread({1: 4, 3: 8, 5: 8})
        for seed in range(30):
            draw = draw_teams(pool, random.Random(seed))
            assert gap(draw, pool) == 0
            assert [c[1] for c in pote_counts(draw, pool)] == [1, 1, 1, 1], seed

    def test_leftover_strong_players_go_to_weaker_teams(self) -> None:
        # Two pote-1 players: the two teams without one should not also get the extra pote 2.
        pool = spread({1: 2, 2: 6, 3: 4, 4: 4, 5: 4})
        for seed in range(30):
            counts = pote_counts(draw_teams(pool, random.Random(seed)), pool)
            for c in counts:
                assert not (c[1] == 1 and c[2] == 2), (seed, counts)

    def test_extra_players_in_goal_leave_teams_of_five(self) -> None:
        rng = random.Random(2026)
        for _ in range(40):
            pool = players(*(rng.randint(1, 5) for _ in range(rng.choice([20, 21, 22]))))
            draw = draw_teams(pool, rng)
            assert sorted(len(team) for team in draw.teams) == [5, 5, 5, 5]
            assert len(set(draw.in_goal)) == len(pool) - 20

    def test_short_list_spreads_players_evenly(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 2})
        draw = draw_teams(pool, random.Random(3))
        assert sorted(len(team) for team in draw.teams) == [4, 4, 5, 5]
        assert draw.in_goal == ()

    def test_needs_a_player_per_team(self) -> None:
        with pytest.raises(DrawError, match="pelo menos 4"):
            draw_teams(players(1, 2, 3), random.Random(0))

    def test_duplicates_are_rejected(self) -> None:
        twice = DrawPlayer(999, 3)
        with pytest.raises(ValueError, match="only once"):
            draw_teams([twice, twice, *players(1, 2, 3)], random.Random(0))


class TestDrawWithGroups:
    def test_a_group_is_never_split(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        group = [pool[0].player_id, pool[5].player_id, pool[10].player_id]
        for seed in range(60):
            draw = draw_teams(pool, random.Random(seed), groups=[group])
            holder = {pid for team in draw.teams if group[0] in team for pid in team}
            assert set(group) <= holder
            assert [len(team) for team in draw.teams] == [5, 5, 5, 5]
            assert sorted(everyone(draw)) == sorted(p.player_id for p in pool)

    def test_a_group_is_kept_together_even_with_a_group_of_five(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        group = [p.player_id for p in pool[:5]]
        for seed in range(20):
            draw = draw_teams(pool, random.Random(seed), groups=[group])
            assert any(set(group) == set(team) for team in draw.teams)

    def test_several_groups_stay_whole(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        groups = [
            [pool[0].player_id, pool[1].player_id],
            [pool[2].player_id, pool[3].player_id, pool[4].player_id],
            [pool[8].player_id, pool[9].player_id],
        ]
        for seed in range(40):
            draw = draw_teams(pool, random.Random(seed), groups=groups)
            for group in groups:
                assert sum(set(group) <= set(team) for team in draw.teams) == 1

    def test_the_rest_of_the_teams_stays_balanced_by_pote(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        groups = [[pool[0].player_id, pool[4].player_id]]  # a pote 1 and a pote 2
        for seed in range(40):
            counts = pote_counts(draw_teams(pool, random.Random(seed), groups=groups), pool)
            for pote in range(1, 6):
                per_team = [c[pote] for c in counts]
                assert max(per_team) - min(per_team) <= 1

    def test_a_group_goes_to_goal_only_whole_and_only_without_singles(self) -> None:
        # 22 line players, 2 for the goal: players alone go first.
        pool = players(*([3] * 22))
        group = [pool[0].player_id, pool[1].player_id]
        for seed in range(30):
            draw = draw_teams(pool, random.Random(seed), groups=[group])
            assert not set(group) & set(draw.in_goal)
        # Four groups of five fill the teams and the pair left over has to complete the goal.
        ids = [p.player_id for p in pool]
        fives = [ids[i : i + 5] for i in range(0, 20, 5)]
        draw = draw_teams(pool, random.Random(1), groups=[*fives, ids[20:]])
        assert set(draw.in_goal) == set(ids[20:])

    def test_no_way_to_fill_the_goal_without_splitting_a_group_is_a_conflict(self) -> None:
        pool = players(*([3] * 21))
        triples = [[pool[i].player_id for i in range(j, j + 3)] for j in range(0, 21, 3)]
        with pytest.raises(DrawConflict) as raised:
            draw_teams(pool, random.Random(1), groups=triples)
        assert raised.value.conflicts[0].kind == "goal"

    def test_a_group_bigger_than_a_team_is_a_conflict(self) -> None:
        pool = players(*([3] * 20))
        group = [p.player_id for p in pool[:6]]
        with pytest.raises(DrawConflict) as raised:
            draw_teams(pool, random.Random(1), groups=[group])
        (conflict,) = raised.value.conflicts
        assert (conflict.kind, conflict.groups, conflict.capacity) == (
            "too_big",
            (tuple(group),),
            5,
        )

    def test_groups_that_do_not_fit_together_are_a_conflict(self) -> None:
        pool = players(*([3] * 20))
        triples = [[pool[i].player_id for i in range(j, j + 3)] for j in range(0, 15, 3)]
        with pytest.raises(DrawConflict) as raised:
            draw_teams(pool, random.Random(1), groups=triples)
        assert raised.value.conflicts[0].kind == "no_fit"

    def test_a_bigger_team_can_be_accepted(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        group = [p.player_id for p in pool[:6]]
        draw = draw_teams(pool, random.Random(1), groups=[group], allow_oversized=True)
        assert sorted(len(team) for team in draw.teams) == [4, 5, 5, 6]
        assert any(set(group) <= set(team) for team in draw.teams)
        assert draw.warnings[0].kind == "count"

    def test_a_balanced_draw_has_no_warning(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        assert draw_teams(pool, random.Random(3)).warnings == ()

    def test_a_group_of_the_same_pote_can_skew_the_teams_and_says_so(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        best = [p.player_id for p in pool[:4]]  # four pote 1 players together
        draw = draw_teams(pool, random.Random(2), groups=[best])
        assert {w.kind for w in draw.warnings} >= {"pote"}

    def test_a_group_with_a_stranger_is_rejected(self) -> None:
        pool = players(*([3] * 20))
        with pytest.raises(ValueError, match="not being drawn"):
            draw_teams(pool, random.Random(1), groups=[[pool[0].player_id, 9999]])

    def test_a_player_in_two_groups_is_rejected(self) -> None:
        pool = players(*([3] * 20))
        ids = [p.player_id for p in pool]
        with pytest.raises(ValueError, match="only one group"):
            draw_teams(pool, random.Random(1), groups=[ids[:2], ids[1:3]])

    def test_same_seed_same_draw_with_groups(self) -> None:
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        groups = [[pool[0].player_id, pool[1].player_id]]
        assert draw_teams(pool, random.Random(5), groups=groups) == draw_teams(
            pool, random.Random(5), groups=groups
        )

    def test_a_draw_without_groups_never_has_the_wrong_sizes(self) -> None:
        for seed in range(20):
            rng = random.Random(seed)
            pool = players(*(rng.randint(1, 5) for _ in range(rng.randint(18, 22))))
            assert all(w.kind != "count" for w in draw_teams(pool, rng).warnings), seed

    def test_pote_sums_are_as_close_as_possible_with_groups(self) -> None:
        for seed in range(120):
            rng = random.Random(seed)
            team_count, team_size = rng.choice([(2, 5), (3, 4), (4, 3), (3, 3)])
            pool = players(*(rng.randint(1, 5) for _ in range(team_count * team_size)))
            ids = [p.player_id for p in pool]
            rng.shuffle(ids)
            groups = [ids[:2], ids[2:5]] if rng.random() < 0.5 else [ids[:3]]
            draw = draw_teams(pool, rng, groups=groups, team_count=team_count, team_size=team_size)
            assert gap(draw, pool) == best_gap(pool, groups, team_count, team_size), seed

    def test_it_is_never_worse_than_the_plain_deal_with_groups(self) -> None:
        for seed in range(40):
            rng = random.Random(seed)
            pool = players(*(rng.randint(1, 5) for _ in range(rng.choice([20, 21, 22]))))
            ids = [p.player_id for p in pool]
            rng.shuffle(ids)
            groups = [ids[:2], ids[2:5], ids[5:7]]
            plain = draw_teams(pool, random.Random(seed), groups=groups, balance=False)
            best = draw_teams(pool, random.Random(seed), groups=groups)
            assert gap(best, pool) <= gap(plain, pool), seed
            for group in groups:
                assert sum(set(group) <= set(team) for team in best.teams) == 1

    def test_a_pair_the_diretoria_tied_does_not_unbalance_the_sums(self) -> None:
        # Two pote 1 players together would make a team too strong unless the rest evens it out.
        pool = spread({1: 4, 2: 4, 3: 4, 4: 4, 5: 4})
        pair = [pool[0].player_id, pool[1].player_id]
        for seed in range(30):
            assert gap(draw_teams(pool, random.Random(seed), groups=[pair]), pool) == 0, seed
