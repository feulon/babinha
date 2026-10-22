from matchday.domain.groups import Group, build_groups, rookie_bonds


class TestRookieBonds:
    def test_a_first_timer_is_bound_to_who_invited_them_when_both_play(self) -> None:
        bonds = rookie_bonds({1, 2, 3}, {2: 1, 3: 1}, rookies={2})
        assert bonds == {2: 1}

    def test_a_veteran_is_free(self) -> None:
        assert rookie_bonds({1, 2}, {2: 1}, rookies=set()) == {}

    def test_an_inviter_who_is_not_playing_leaves_the_first_timer_free(self) -> None:
        # Marcos (9) is off the list or keeps goal: he is not among the line players playing.
        assert rookie_bonds({2}, {2: 9}, rookies={2}) == {}

    def test_a_first_timer_without_inviter_is_free(self) -> None:
        assert rookie_bonds({1}, {1: None}, rookies={1}) == {}

    def test_a_released_first_timer_is_free(self) -> None:
        assert rookie_bonds({1, 2}, {2: 1}, rookies={2}, released={2}) == {}


class TestBuildGroups:
    def test_nobody_tied_means_no_group(self) -> None:
        assert build_groups({}, {}, {1, 2, 3}) == ()

    def test_a_manual_group(self) -> None:
        groups = build_groups({10: [3, 1, 2]}, {}, {1, 2, 3, 4})
        assert groups == (Group((1, 2, 3), (), (10,)),)

    def test_a_first_timer_joins_the_inviter(self) -> None:
        groups = build_groups({}, {2: 1}, {1, 2, 3})
        assert groups == (Group((1, 2), ((2, 1),), ()),)
        assert groups[0].rookie_ids == {2}

    def test_a_chain_of_first_timers_is_one_group(self) -> None:
        groups = build_groups({}, {2: 1, 3: 2}, {1, 2, 3})
        assert [g.player_ids for g in groups] == [(1, 2, 3)]

    def test_a_manual_group_and_a_bond_merge(self) -> None:
        # Rafa (1) is in a group of three and invited Bruno (9): they are six together.
        groups = build_groups({10: [1, 2, 3]}, {9: 1}, {1, 2, 3, 9})
        assert groups == (Group((1, 2, 3, 9), ((9, 1),), (10,)),)

    def test_two_manual_groups_sharing_nobody_stay_apart(self) -> None:
        groups = build_groups({1: [1, 2], 2: [3, 4]}, {}, {1, 2, 3, 4})
        assert [g.player_ids for g in groups] == [(1, 2), (3, 4)]

    def test_members_who_left_the_list_are_ignored(self) -> None:
        assert build_groups({1: [1, 2, 3]}, {}, {1, 2}) == (Group((1, 2), (), (1,)),)
        assert build_groups({1: [1, 2]}, {}, {1}) == ()

    def test_a_bond_to_someone_not_playing_is_ignored(self) -> None:
        assert build_groups({}, {2: 9}, {1, 2}) == ()

    def test_groups_are_ordered_by_their_first_player(self) -> None:
        groups = build_groups({1: [8, 9], 2: [1, 2]}, {}, set(range(1, 10)))
        assert [g.player_ids for g in groups] == [(1, 2), (8, 9)]
