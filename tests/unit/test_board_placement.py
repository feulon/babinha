"""The diretoria placing players by hand: order, sections, the spot limit and the automatic rules
that keep running for everyone else."""

from datetime import timedelta

import pytest

from games.domain.roster import (
    Bumped,
    Confirmed,
    Dropped,
    GameCancelled,
    ListContext,
    NobodyToTakeTheSpot,
    NotOnList,
    Overflowed,
    Placed,
    Promoted,
    Section,
    place,
)
from games.domain.types import EntryStatus
from tests.unit.test_roster import (
    BEFORE_SATURDAY_LAST_MINUTE,
    GK,
    LINE,
    SUNDAY_MORNING,
    WEEK,
    A,
    M,
    Week,
)

LIST_LINE = Section(EntryStatus.LISTA, LINE)
LIST_GK = Section(EntryStatus.LISTA, GK)
WAIT_LINE = Section(EntryStatus.ESPERA, LINE)
WAIT_GK = Section(EntryStatus.ESPERA, GK)


class Board(Week):
    def place(self, player_id: int, section: Section, index: int | None = None) -> None:
        self.tick()
        outcome = place(self.roster, player_id, section, index, self.ctx())
        self.roster, self.events = outcome.roster, outcome.events

    def line(self) -> list[int]:
        return [e.player_id for e in self.roster.confirmed(LINE)]

    def keepers(self) -> list[int]:
        return [e.player_id for e in self.roster.confirmed(GK)]


@pytest.fixture
def board() -> Board:
    return Board()


class TestManualOrder:
    def test_moves_a_player_up(self, board: Board) -> None:
        a, b, c = board.join_many(3)
        board.place(c, LIST_LINE, 1)
        assert board.line() == [c, a, b]
        assert board.events == (Placed(c, LIST_LINE, 1),)

    def test_moves_a_player_down(self, board: Board) -> None:
        a, b, c = board.join_many(3)
        board.place(a, LIST_LINE, 2)
        assert board.line() == [b, a, c]

    def test_a_place_past_the_end_means_the_end(self, board: Board) -> None:
        a, b, c = board.join_many(3)
        board.place(a, LIST_LINE, 99)
        assert board.line() == [b, c, a]

    def test_later_sign_ups_still_go_after_the_unplaced(self, board: Board) -> None:
        a, b, c = board.join_many(3)
        board.place(c, LIST_LINE, 1)
        d = board.join()
        assert board.line() == [c, a, b, d]

    def test_the_place_holds_when_someone_above_leaves(self, board: Board) -> None:
        a, b, c, d = board.join_many(4)
        board.place(d, LIST_LINE, 2)
        assert board.line() == [a, d, b, c]
        board.drop(a, by_board=True)
        assert board.line() == [b, d, c]

    def test_placing_next_to_a_placed_player_keeps_both_in_order(self, board: Board) -> None:
        a, b, c, d = board.join_many(4)
        board.place(d, LIST_LINE, 1)
        board.place(c, LIST_LINE, 1)
        assert board.line() == [c, d, a, b]
        board.join()
        assert board.line()[:2] == [c, d]

    def test_mensalista_priority_never_bumps_a_placed_player(self, board: Board) -> None:
        board.join_many(20, player_type=M)
        placed = board.join(player_type=A)
        unplaced = board.join(player_type=A)
        board.place(placed, LIST_LINE, 22)  # at the very end, but placed by the diretoria
        mensalista = board.join(player_type=M)
        assert board.events == (Bumped(unplaced, by_player_id=mensalista), Confirmed(mensalista))
        assert board.status(placed) == EntryStatus.LISTA

    def test_goalkeeper_flex_rule_spares_placed_players(self, board: Board) -> None:
        line = board.join_many(22, player_type=M)
        board.place(line[-1], LIST_LINE, 22)
        keeper = board.join(GK)
        assert board.events == (Bumped(line[-2], by_player_id=keeper), Confirmed(keeper))

    def test_goalkeeper_waits_when_every_line_player_was_placed(self, board: Board) -> None:
        line = board.join_many(22)
        for index, player in enumerate(line, start=1):
            board.place(player, LIST_LINE, index)
        board.join(GK)
        assert board.counts() == (22, 0)

    def test_manual_order_of_the_waitlist_decides_who_is_promoted(self, board: Board) -> None:
        board.join_many(22)
        first, second, third = board.join_many(3)
        board.place(third, WAIT_LINE, 1)
        assert board.queue() == [third, first, second]
        board.drop(board.line()[0])
        assert board.events[-1] == Promoted(third)

    def test_the_waitlist_keeps_the_automatic_order_around_a_placed_player(
        self, board: Board
    ) -> None:
        board.join_many(22, player_type=M)
        a, b = board.join_many(2)
        board.place(b, WAIT_LINE, 1)
        mensalista = board.join(player_type=M)  # free period: mensalistas wait first
        assert board.queue() == [b, mensalista, a]


class TestSections:
    def test_promotes_from_the_waitlist_to_a_spot_left_free_after_kickoff(
        self, board: Board
    ) -> None:
        line = board.join_many(22)
        waiting = board.join()
        board.now = SUNDAY_MORNING + timedelta(hours=3)
        board.drop(line[0], by_board=True)  # no automatic promotion once the game started
        assert board.status(waiting) == EntryStatus.ESPERA
        board.place(waiting, LIST_LINE)
        assert board.line()[-1] == waiting
        assert board.counts() == (22, 0)

    def test_promoting_into_a_full_list_sends_the_last_to_the_top_of_the_queue(
        self, board: Board
    ) -> None:
        line = board.join_many(22)
        first, second = board.join_many(2)
        board.place(second, LIST_LINE)
        assert board.events == (Placed(second, LIST_LINE, 22), Overflowed(line[-1]))
        assert board.line()[-1] == second
        assert board.queue() == [line[-1], first]
        assert board.roster.get(second).promoted

    def test_promoting_to_a_chosen_place(self, board: Board) -> None:
        line = board.join_many(22)
        waiting = board.join()
        board.place(waiting, LIST_LINE, 3)
        assert board.line()[:3] == [line[0], line[1], waiting]
        assert board.queue() == [line[-1]]

    def test_sending_to_the_waitlist_gives_the_spot_to_the_next_in_line(self, board: Board) -> None:
        line = board.join_many(22)
        waiting = board.join()
        board.place(line[0], WAIT_LINE)
        assert board.events == (Placed(line[0], WAIT_LINE, 1), Promoted(waiting))
        assert board.queue() == [line[0]]
        assert not any(isinstance(e, Dropped) for e in board.events)

    def test_sending_to_an_empty_waitlist_is_refused(self, board: Board) -> None:
        line = board.join_many(3)
        with pytest.raises(NobodyToTakeTheSpot):
            board.place(line[0], WAIT_LINE)

    def test_line_player_to_the_goal(self, board: Board) -> None:
        line = board.join_many(3)
        board.place(line[1], LIST_GK)
        assert board.keepers() == [line[1]]
        assert board.roster.get(line[1]).position == GK

    def test_a_fifth_goalkeeper_pushes_the_last_keeper_to_the_queue(self, board: Board) -> None:
        keepers = board.join_many(4, GK)
        line = board.join()
        board.place(line, LIST_GK, 1)
        assert board.keepers() == [line, *keepers[:3]]
        assert board.queue(GK) == [keepers[3]]

    def test_moving_past_the_limit_sends_the_moved_player_to_the_queue(self, board: Board) -> None:
        board.join_many(4, GK)
        line = board.join()
        board.place(line, LIST_GK, 5)
        assert board.events[-1] == Overflowed(line)
        assert board.queue(GK) == [line]

    def test_the_spot_limit_always_holds(self, board: Board) -> None:
        keepers = board.join_many(4, GK)
        line = board.join_many(22)  # 20 on the list, 2 waiting
        for player in line[-4:]:
            board.place(player, LIST_GK, 1)
        assert board.roster.within_capacity()
        assert board.counts() == (18, 4)
        assert set(board.queue(GK)) == set(keepers)


class TestBoardPowers:
    def test_works_after_the_saturday_deadline_with_no_fine(self, board: Board) -> None:
        line = board.join_many(22)
        waiting = board.join()
        board.now = BEFORE_SATURDAY_LAST_MINUTE + timedelta(minutes=5)
        board.place(line[0], WAIT_LINE)
        assert board.status(waiting) == EntryStatus.LISTA
        assert not any(isinstance(e, Dropped) for e in board.events)

    def test_works_after_kickoff_without_automatic_promotion(self, board: Board) -> None:
        line = board.join_many(22)
        board.join()
        board.now = SUNDAY_MORNING + timedelta(hours=3)
        board.place(line[0], WAIT_LINE)
        assert board.counts() == (21, 0)
        assert board.queue()[0] == line[0]

    def test_not_on_a_cancelled_sunday(self, board: Board) -> None:
        (player,) = board.join_many(1)
        ctx = ListContext(WEEK, board.now, cancelled=True)
        with pytest.raises(GameCancelled):
            place(board.roster, player, LIST_LINE, 1, ctx)

    def test_only_players_on_the_list_or_the_waitlist(self, board: Board) -> None:
        (player,) = board.join_many(1)
        board.drop(player)
        with pytest.raises(NotOnList):
            board.place(player, LIST_LINE, 1)

    def test_leaving_the_section_forgets_the_manual_place(self, board: Board) -> None:
        a, b = board.join_many(2)
        board.place(b, LIST_LINE, 1)
        board.drop(b)
        board.join_as(b, LINE, A)
        assert board.line() == [a, b]
        assert board.roster.get(b).board_rank is None

    def test_a_section_is_never_the_dropped_ones(self) -> None:
        with pytest.raises(ValueError, match="section"):
            Section(EntryStatus.SAIU, LINE)
