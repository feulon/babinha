import random

from matchday.domain.packing import fits, oversized_capacities, place_groups


class TestPlaceGroups:
    def test_every_group_lands_whole_within_the_room(self) -> None:
        sizes, capacities = [3, 2, 2, 5], [5, 5, 5, 5]
        for seed in range(30):
            placed = place_groups(sizes, capacities, random.Random(seed))
            assert placed is not None
            used = [sum(s for s, t in zip(sizes, placed, strict=True) if t == i) for i in range(4)]
            assert all(u <= c for u, c in zip(used, capacities, strict=True))

    def test_the_placement_is_random(self) -> None:
        placements = {
            tuple(place_groups([2, 2], [5, 5, 5, 5], random.Random(seed)) or ())
            for seed in range(30)
        }
        assert len(placements) > 3

    def test_impossible_when_a_group_is_bigger_than_any_team(self) -> None:
        assert place_groups([6], [5, 5, 5, 5], random.Random(1)) is None

    def test_impossible_when_the_groups_do_not_fit_together(self) -> None:
        # Five groups of three, four teams of five: one team holds only one of them.
        assert not fits([3, 3, 3, 3, 3], [5, 5, 5, 5])

    def test_a_tight_fit_is_found(self) -> None:
        assert fits([3, 3, 2, 2, 2, 2, 3, 3], [5, 5, 5, 5])


class TestOversizedCapacities:
    def test_a_group_of_six_takes_a_player_from_another_team(self) -> None:
        assert oversized_capacities([5, 5, 5, 5], [6]) == (6, 5, 5, 4)

    def test_a_group_of_seven(self) -> None:
        assert oversized_capacities([5, 5, 5, 5], [7]) == (7, 5, 4, 4)

    def test_nothing_changes_when_every_group_fits(self) -> None:
        assert oversized_capacities([5, 5, 5, 4], [5, 2]) == (5, 5, 5, 4)
