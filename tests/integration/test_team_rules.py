"""Rules for drawing the teams: first-timers with who invited them, groups made by hand, the
conflicts between them and the ways out."""

import random
from collections.abc import Callable
from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Player
from games import services as games
from games.domain.types import EntryStatus
from games.models import Attendance, GameDay, ListEntry
from matchday import grouping as grouping_module
from matchday import services, team_rules
from matchday.models import Lineup, LineupSpot, RookieRelease, TeamRules, TogetherGroup
from matchday.services import MatchdayError, RulesConflict
from tests.conftest import Sunday

pytestmark = pytest.mark.django_db

LAST_SUNDAY = date(2026, 10, 11)


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


def invite(player: Player, inviter: Player) -> None:
    Player.objects.filter(pk=player.pk).update(referred_by=inviter)
    player.referred_by = inviter


def played_before(*players: Player, attendance: str = Attendance.PRESENTE) -> None:
    """Mark players present (or absent) on an earlier Sunday: they are no longer first-timers."""
    game_day, _ = GameDay.objects.get_or_create(date=LAST_SUNDAY)
    for player in players:
        ListEntry.objects.create(
            game_day=game_day,
            player=player,
            position=player.position,
            status=EntryStatus.LISTA.value,
            joined_at=game_day.created_at,
            confirmed_at=game_day.created_at,
            attendance=attendance,
        )


def team_of(lineup: Lineup, player: Player) -> int | None:
    spot = LineupSpot.objects.select_related("team").get(lineup=lineup, player=player)
    return spot.team.number if spot.team else None


def draw(sunday: Sunday, board: Player, seed: int = 1) -> Lineup:
    return services.draw(sunday.game_day.pk, board, random.Random(seed))


def together(lineup: Lineup, *players: Player) -> bool:
    return len({team_of(lineup, player) for player in players}) == 1


def grouping(sunday: Sunday) -> grouping_module.Grouping:
    return services.grouping_of(sunday.game_day)


class TestFirstTimers:
    def test_a_first_timer_plays_with_who_invited_them(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        for seed in range(25):
            assert together(draw(sunday, board_member, seed), rafa, bruno)

    def test_the_inviter_may_be_a_first_timer_too(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno, caio = sunday.line[0], sunday.line[7], sunday.line[9]
        invite(bruno, rafa)
        invite(caio, bruno)
        for seed in range(10):
            assert together(draw(sunday, board_member, seed), rafa, bruno, caio)

    def test_a_veteran_is_free(self, sunday: Sunday, board_member: Player) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        played_before(bruno)
        assert grouping(sunday).groups == ()
        split = {together(draw(sunday, board_member, seed), rafa, bruno) for seed in range(25)}
        assert split == {True, False}

    def test_an_absence_on_an_earlier_sunday_does_not_make_a_veteran(self, sunday: Sunday) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        played_before(bruno, attendance=Attendance.FALTOU)
        assert [g.player_ids for g in grouping(sunday).groups] == [(rafa.pk, bruno.pk)]

    def test_only_earlier_sundays_count(self, sunday: Sunday) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        entry_day = sunday.game_day
        ListEntry.objects.filter(game_day=entry_day, player=bruno).update(
            attendance=Attendance.PRESENTE
        )
        assert [g.player_ids for g in grouping(sunday).groups] == [(rafa.pk, bruno.pk)]

    def test_an_inviter_off_the_list_leaves_the_first_timer_free(self, sunday: Sunday) -> None:
        bruno = sunday.line[7]
        outsider = Player.objects.create_player(
            phone="11988880001", password="ab12", name="Marcos", nickname="Marcos", referred_by=None
        )
        invite(bruno, outsider)
        assert grouping(sunday).groups == ()
        (row,) = (r for r in grouping(sunday).rookie_rows if r.player == bruno)
        assert row.state == "absent"

    def test_an_inviter_who_keeps_goal_leaves_the_first_timer_free(self, sunday: Sunday) -> None:
        bruno = sunday.line[7]
        invite(bruno, sunday.goalkeepers[0])
        assert grouping(sunday).groups == ()
        (row,) = (r for r in grouping(sunday).rookie_rows if r.player == bruno)
        assert row.state == "goalkeeper"

    def test_the_bond_ends_when_the_inviter_leaves_the_list(self, sunday: Sunday) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        assert grouping(sunday).groups
        games.leave_list(sunday.game_day.pk, rafa)
        assert grouping(sunday).groups == ()

    def test_every_invited_first_timer_is_listed_with_what_the_rule_does(
        self, sunday: Sunday
    ) -> None:
        rows = grouping(sunday).rookie_rows
        assert len(rows) == 20
        assert {(r.inviter.name, r.state) for r in rows} == {("Presidente", "absent")}

    def test_the_switch_turns_the_rule_off_and_on(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        team_rules.set_rookie_rule(sunday.game_day.pk, board_member, False)
        assert grouping(sunday).groups == ()
        assert {r.state for r in grouping(sunday).rookie_rows} == {"off"}
        team_rules.set_rookie_rule(sunday.game_day.pk, board_member, True)
        assert len(grouping(sunday).groups) == 1

    def test_a_released_first_timer_is_free_only_on_that_sunday(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        team_rules.release_rookie(sunday.game_day.pk, board_member, bruno.pk)
        assert grouping(sunday).groups == ()
        assert RookieRelease.objects.get().released_by == board_member
        team_rules.restore_rookie(sunday.game_day.pk, board_member, bruno.pk)
        assert len(grouping(sunday).groups) == 1

    def test_releasing_needs_an_active_bond(self, sunday: Sunday, board_member: Player) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        with pytest.raises(MatchdayError, match="não é estreante"):
            team_rules.release_rookie(sunday.game_day.pk, board_member, sunday.goalkeepers[0].pk)
        invite(bruno, rafa)
        team_rules.release_rookie(sunday.game_day.pk, board_member, bruno.pk)
        with pytest.raises(MatchdayError, match="já está livre"):
            team_rules.release_rookie(sunday.game_day.pk, board_member, bruno.pk)
        with pytest.raises(MatchdayError, match="não foi liberado"):
            team_rules.restore_rookie(sunday.game_day.pk, board_member, rafa.pk)


class TestManualGroups:
    def test_a_group_plays_in_the_same_team(self, sunday: Sunday, board_member: Player) -> None:
        trio = sunday.line[2:5]
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in trio], "Carro")
        for seed in range(25):
            assert together(draw(sunday, board_member, seed), *trio)

    def test_the_group_has_a_label(self, sunday: Sunday, board_member: Player) -> None:
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]])
        team_rules.create_group(
            sunday.game_day.pk, board_member, [p.pk for p in sunday.line[2:5]], "  Mesmo   carro "
        )
        assert [g.label for g in grouping(sunday).manual] == ["Dupla", "Mesmo carro"]

    @pytest.mark.parametrize("count", [0, 1, 6])
    def test_two_to_five_players(self, sunday: Sunday, board_member: Player, count: int) -> None:
        with pytest.raises(MatchdayError, match="2 a 5"):
            team_rules.create_group(
                sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:count]]
            )

    def test_the_same_player_twice_counts_once(self, sunday: Sunday, board_member: Player) -> None:
        with pytest.raises(MatchdayError, match="2 a 5"):
            team_rules.create_group(
                sunday.game_day.pk, board_member, [sunday.line[0].pk, sunday.line[0].pk]
            )

    def test_only_line_players_on_the_list(self, sunday: Sunday, board_member: Player) -> None:
        stranger = Player.objects.create_player(
            phone="11988880002", password="ab12", name="Fora", nickname="Fora", referred_by=None
        )
        for other in (sunday.goalkeepers[0], stranger):
            with pytest.raises(MatchdayError, match="linha da lista"):
                team_rules.create_group(
                    sunday.game_day.pk, board_member, [sunday.line[0].pk, other.pk]
                )

    def test_a_player_is_in_one_group_a_day(self, sunday: Sunday, board_member: Player) -> None:
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]])
        with pytest.raises(MatchdayError, match=f"{sunday.line[1].name} já está num grupo"):
            team_rules.create_group(
                sunday.game_day.pk, board_member, [sunday.line[1].pk, sunday.line[2].pk]
            )

    def test_the_name_is_limited(self, sunday: Sunday, board_member: Player) -> None:
        with pytest.raises(MatchdayError, match="40 caracteres"):
            team_rules.create_group(
                sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]], "x" * 41
            )

    def test_deleting_a_group_frees_its_players(self, sunday: Sunday, board_member: Player) -> None:
        group = team_rules.create_group(
            sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]]
        )
        team_rules.delete_group(sunday.game_day.pk, board_member, group.pk)
        assert not TogetherGroup.objects.exists()
        assert grouping(sunday).groups == ()
        with pytest.raises(MatchdayError, match="não encontrado"):
            team_rules.delete_group(sunday.game_day.pk, board_member, group.pk)

    def test_a_group_shrinks_when_a_member_leaves_the_list(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        trio = sunday.line[:3]
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in trio])
        games.leave_list(sunday.game_day.pk, trio[2])
        assert [g.player_ids for g in grouping(sunday).groups] == [(trio[0].pk, trio[1].pk)]
        games.leave_list(sunday.game_day.pk, trio[1])
        assert grouping(sunday).groups == ()

    def test_groups_belong_to_their_sunday(self, sunday: Sunday, board_member: Player) -> None:
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]])
        next_day = GameDay.objects.create(date=sunday.game_day.date + timedelta(days=7))
        assert grouping_module.rules_for(next_day).rookies_follow_inviter
        assert not TogetherGroup.objects.filter(game_day=next_day).exists()

    def test_only_the_board_edits_the_rules(self, sunday: Sunday) -> None:
        with pytest.raises(PermissionDenied):
            team_rules.create_group(sunday.game_day.pk, sunday.line[0], [sunday.line[1].pk] * 2)
        with pytest.raises(PermissionDenied):
            team_rules.set_rookie_rule(sunday.game_day.pk, sunday.line[0], False)

    def test_a_cancelled_sunday_has_no_rules_to_edit(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        with pytest.raises(MatchdayError, match="cancelado"):
            team_rules.set_rookie_rule(sunday.game_day.pk, board_member, False)


class TestConflicts:
    """Rafa, four friends and Bruno (a first-timer invited by Rafa) are six: a team holds five."""

    @pytest.fixture
    def six(self, sunday: Sunday, board_member: Player) -> list[Player]:
        rafa, *friends = sunday.line[:5]
        bruno = sunday.line[10]
        invite(bruno, rafa)
        team_rules.create_group(
            sunday.game_day.pk, board_member, [rafa.pk, *(f.pk for f in friends)]
        )
        return [rafa, *friends, bruno]

    def test_the_draw_says_why_it_cannot_be_done(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        with pytest.raises(RulesConflict) as raised:
            draw(sunday, board_member)
        (conflict,) = raised.value.conflicts
        assert (conflict.kind, conflict.capacity) == ("too_big", 5)
        assert set(conflict.groups[0]) == {p.pk for p in six}
        assert not Lineup.objects.exists()

    def test_the_conflict_is_described_with_names(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        conflicts = team_rules.conflicts_of(grouping(sunday))
        text = team_rules.describe(grouping(sunday), conflicts[0])
        rafa, bruno = six[0], six[5]
        assert f"{', '.join(p.name for p in six[:5])} mais o estreante {bruno.name}" in text
        assert f"(joga com {rafa.name})" in text
        assert "tem 6 jogadores" in text
        assert "Cada time tem 5 vagas" in text

    def test_the_ways_out(self, sunday: Sunday, six: list[Player]) -> None:
        grouping_now = grouping(sunday)
        options = team_rules.resolutions(grouping_now, team_rules.conflicts_of(grouping_now))
        keys = [o.key for o in options]
        # Let Bruno play elsewhere (nobody leaves the group), take out the friend added last (not
        # Rafa, who anchors Bruno), accept a team of 6.
        assert keys == [
            f"liberar:{six[5].pk}",
            f"remover:{six[4].pk}",
            "aceitar",
        ]
        assert [o.suggested for o in options] == [True, False, False]
        assert options[0].title == f"Deixar {six[5].name} jogar em outro time"
        assert "5 · 5 · 5 · 5" in options[0].effect
        assert "5 · 5 · 5 · 5" in options[1].effect
        assert "6 · 5 · 5 · 4" in options[2].effect

    def test_each_way_out_says_what_changed(self, sunday: Sunday, six: list[Player]) -> None:
        grouping_now = grouping(sunday)
        options = team_rules.resolutions(grouping_now, team_rules.conflicts_of(grouping_now))
        done = {o.key.split(":")[0]: o.done for o in options}
        assert done["liberar"] == f"{six[5].name} vai poder jogar em outro time, só neste domingo."
        assert done["remover"] == f"{six[4].name} saiu do grupo."
        assert done["aceitar"] == "Um time vai jogar com 6 jogadores."

    def test_a_group_that_shrinks_to_one_is_said_to_be_gone(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        # A pair plus four first-timers bound to one of them is a group of six.
        anchor, friend, *rookies = sunday.line[:6]
        team_rules.create_group(sunday.game_day.pk, board_member, [anchor.pk, friend.pk])
        for rookie in rookies:
            invite(rookie, anchor)
        grouping_now = grouping(sunday)
        options = team_rules.resolutions(grouping_now, team_rules.conflicts_of(grouping_now))
        removal = next(o for o in options if o.key.startswith("remover"))
        assert removal.key == f"remover:{friend.pk}"
        assert removal.done == f"{friend.name} saiu do grupo. O grupo foi desfeito."

    def test_the_problem_says_why_and_offers_the_ways_out(
        self, sunday: Sunday, six: list[Player]
    ) -> None:
        problem = team_rules.rules_problem(grouping(sunday))
        assert problem is not None
        assert "tem 6 jogadores" in problem.lines[0]
        assert problem.resolutions[0].key == f"liberar:{six[5].pk}"

    def test_no_problem_when_the_groups_can_be_dealt(self, sunday: Sunday) -> None:
        assert team_rules.rules_problem(grouping(sunday)) is None

    def test_taking_a_member_out_draws(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        lineup = team_rules.resolve_conflict(
            sunday.game_day.pk, board_member, f"remover:{six[4].pk}", random.Random(2)
        )
        assert together(lineup, *six[:4], six[5])
        assert [g.size for g in grouping(sunday).groups] == [5]
        assert six[4] in grouping(sunday).line

    def test_releasing_the_first_timer_draws(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        lineup = team_rules.resolve_conflict(
            sunday.game_day.pk, board_member, f"liberar:{six[5].pk}", random.Random(2)
        )
        assert together(lineup, *six[:5])
        assert RookieRelease.objects.filter(player=six[5]).exists()

    def test_accepting_a_bigger_team_draws_one_team_of_six(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        lineup = team_rules.resolve_conflict(
            sunday.game_day.pk, board_member, "aceitar", random.Random(2)
        )
        assert together(lineup, *six)
        sizes = sorted(lineup.spots.filter(team=t).count() for t in lineup.teams.all())
        assert sizes == [4, 5, 5, 6]
        assert TeamRules.objects.get().allow_oversized_team
        # The acceptance holds for a redraw, and confirming the teams is allowed.
        again = draw(sunday, board_member, 9)
        assert together(again, *six)
        services.confirm(sunday.game_day.pk, board_member)

    def test_changing_the_rules_forgets_the_acceptance(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        team_rules.resolve_conflict(sunday.game_day.pk, board_member, "aceitar", random.Random(2))
        team_rules.set_rookie_rule(sunday.game_day.pk, board_member, True)
        assert not TeamRules.objects.get().allow_oversized_team
        with pytest.raises(RulesConflict):
            draw(sunday, board_member)

    def test_a_stale_choice_is_refused(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        with pytest.raises(MatchdayError, match="não vale mais"):
            team_rules.resolve_conflict(sunday.game_day.pk, board_member, "remover:999999")
        assert not Lineup.objects.exists()
        assert len(grouping(sunday).manual[0].players) == 5

    def test_a_way_out_that_does_not_work_is_not_offered(
        self, sunday: Sunday, board_member: Player, six: list[Player]
    ) -> None:
        # A second first-timer bound to the group keeps it too big even without Bruno.
        invite(sunday.line[11], six[1])
        grouping_now = grouping(sunday)
        keys = [
            o.key
            for o in team_rules.resolutions(grouping_now, team_rules.conflicts_of(grouping_now))
        ]
        assert f"liberar:{six[5].pk}" not in keys
        with pytest.raises(MatchdayError, match="não vale mais"):
            team_rules.resolve_conflict(sunday.game_day.pk, board_member, f"liberar:{six[5].pk}")
        assert not RookieRelease.objects.exists()

    def test_groups_that_fit_one_by_one_but_not_together(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        for n in range(5):
            members = [p.pk for p in sunday.line[n * 3 : n * 3 + 3]]
            team_rules.create_group(sunday.game_day.pk, board_member, members)
        with pytest.raises(RulesConflict) as raised:
            draw(sunday, board_member)
        assert raised.value.conflicts[0].kind == "no_fit"
        text = team_rules.describe(grouping(sunday), raised.value.conflicts[0])
        assert "juntos não cabem" in text

    def test_no_conflict_means_the_resolution_just_draws(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        lineup = team_rules.resolve_conflict(sunday.game_day.pk, board_member, "aceitar")
        assert lineup.spots.count() == 20


class TestAdjustAndConfirmKeepGroupsWhole:
    def test_a_hand_adjustment_cannot_split_a_group(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        lineup = draw(sunday, board_member)
        current = {s.player_id: s.team.number for s in lineup.spots.select_related("team")}
        other = 1 if current[rafa.pk] != 1 else 2
        with pytest.raises(MatchdayError, match=f"{rafa.name} \\+ {bruno.name}"):
            services.assign(sunday.game_day.pk, board_member, {**current, bruno.pk: other})
        assert team_of(lineup, bruno) == current[bruno.pk]

    def test_the_whole_group_can_move(self, sunday: Sunday, board_member: Player) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        lineup = draw(sunday, board_member)
        current = {s.player_id: s.team.number for s in lineup.spots.select_related("team")}
        other = 1 if current[rafa.pk] != 1 else 2
        moved = {**current, bruno.pk: other, rafa.pk: other}
        services.assign(sunday.game_day.pk, board_member, moved)
        assert together(lineup, rafa, bruno)
        assert team_of(lineup, rafa) == other

    def test_a_released_first_timer_can_move_alone(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        lineup = draw(sunday, board_member)
        team_rules.release_rookie(sunday.game_day.pk, board_member, bruno.pk)
        current = {s.player_id: s.team.number for s in lineup.spots.select_related("team")}
        other = 1 if current[bruno.pk] != 1 else 2
        services.assign(sunday.game_day.pk, board_member, {**current, bruno.pk: other})
        assert team_of(lineup, bruno) == other

    def test_the_group_goes_to_goal_whole_or_not_at_all(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday(line=21, goalkeepers=1)
        pair = sunday.line[:2]
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in pair])
        lineup = draw(sunday, board_member)
        current = {
            s.player_id: s.team.number if s.team else None
            for s in lineup.spots.select_related("team")
        }
        with pytest.raises(MatchdayError, match="mesmo time"):
            services.assign(sunday.game_day.pk, board_member, {**current, pair[0].pk: None})

    def test_confirming_needs_the_groups_whole(self, sunday: Sunday, board_member: Player) -> None:
        draw(sunday, board_member)
        # A group made after the draw splits teams that are already drawn.
        pair = [
            next(p for p in sunday.line if team_of(Lineup.objects.get(), p) == n) for n in (1, 2)
        ]
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in pair])
        with pytest.raises(MatchdayError, match="mesmo time"):
            services.confirm(sunday.game_day.pk, board_member)
        assert not Lineup.objects.get().is_confirmed

    def test_confirming_needs_a_group_that_fits_a_team(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        group = sunday.line[1:5]
        team_rules.create_group(sunday.game_day.pk, board_member, [rafa.pk, *(p.pk for p in group)])
        with pytest.raises(RulesConflict):
            draw(sunday, board_member)
        lineup = team_rules.resolve_conflict(
            sunday.game_day.pk, board_member, "aceitar", random.Random(4)
        )
        # The acceptance is forgotten when the rules change; the six are still together.
        team_rules.set_rookie_rule(sunday.game_day.pk, board_member, True)
        with pytest.raises(MatchdayError, match="tem 6 jogadores e cada time tem 5 vagas"):
            services.confirm(sunday.game_day.pk, board_member)
        assert lineup.pk
