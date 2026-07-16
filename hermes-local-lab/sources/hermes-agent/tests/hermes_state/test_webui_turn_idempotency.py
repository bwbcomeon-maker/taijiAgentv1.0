from hermes_state import SessionDB


def test_append_message_is_idempotent_for_session_role_and_platform_id(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="webui-session", source="webui")

    first_id = db.append_message(
        session_id="webui-session",
        role="user",
        content="first payload",
        platform_message_id="webui-turn:turn-123",
    )
    second_id = db.append_message(
        session_id="webui-session",
        role="user",
        content="retry payload must not replace the accepted turn",
        platform_message_id="webui-turn:turn-123",
    )

    rows = db.get_messages("webui-session")
    session = db.get_session("webui-session")
    assert first_id == second_id
    assert [(row["role"], row["content"]) for row in rows] == [("user", "first payload")]
    assert session["message_count"] == 1


def test_same_platform_id_is_independent_across_roles(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="webui-session", source="webui")

    db.append_message(
        session_id="webui-session",
        role="user",
        content="question",
        platform_message_id="webui-turn:turn-123",
    )
    db.append_message(
        session_id="webui-session",
        role="assistant",
        content="answer",
        platform_message_id="webui-turn:turn-123",
    )

    assert [row["role"] for row in db.get_messages("webui-session")] == ["user", "assistant"]
    assert db.get_session("webui-session")["message_count"] == 2


def test_replace_messages_can_create_new_webui_truth_in_one_transaction(tmp_path):
    db = SessionDB(tmp_path / "state.db")

    db.replace_messages(
        "copied-webui-session",
        [
            {
                "role": "user",
                "content": "copied question",
                "platform_message_id": "webui-turn:copied-turn",
            },
            {"role": "assistant", "content": "copied answer"},
        ],
        ensure_source="webui",
        ensure_model="test-model",
    )

    session = db.get_session("copied-webui-session")
    assert session["source"] == "webui"
    assert session["model"] == "test-model"
    assert session["message_count"] == 2
    assert [row["content"] for row in db.get_messages("copied-webui-session")] == [
        "copied question",
        "copied answer",
    ]


def test_replace_messages_stably_deduplicates_platform_ids_in_transaction(tmp_path):
    db = SessionDB(tmp_path / "state.db")

    db.replace_messages(
        "deduped-webui-session",
        [
            {
                "role": "user",
                "content": "accepted first payload",
                "platform_message_id": "webui-turn:duplicate",
            },
            {
                "role": "user",
                "content": "later duplicate must be ignored",
                "platform_message_id": "webui-turn:duplicate",
            },
            {
                "role": "assistant",
                "content": "same id is independent for another role",
                "platform_message_id": "webui-turn:duplicate",
            },
            {
                "role": "assistant",
                "content": "later assistant duplicate must be ignored",
                "platform_message_id": "webui-turn:duplicate",
            },
        ],
        ensure_source="webui",
    )

    rows = db.get_messages("deduped-webui-session")
    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "accepted first payload"),
        ("assistant", "same id is independent for another role"),
    ]
    assert db.get_session("deduped-webui-session")["message_count"] == 2


def test_fifty_completed_webui_turns_have_balanced_roles_without_duplicates(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="long-webui-session", source="webui")

    for index in range(50):
        platform_id = f"webui-turn:turn-{index}"
        db.append_message(
            session_id="long-webui-session",
            role="user",
            content=f"question {index}",
            platform_message_id=platform_id,
        )
        # Worker retry/flush of the same accepted turn must remain a no-op.
        db.append_message(
            session_id="long-webui-session",
            role="user",
            content=f"duplicate question {index}",
            platform_message_id=platform_id,
        )
        db.append_message(
            session_id="long-webui-session",
            role="assistant",
            content=f"answer {index}",
        )

    rows = db.get_messages("long-webui-session")
    assert sum(row["role"] == "user" for row in rows) == 50
    assert sum(row["role"] == "assistant" for row in rows) == 50
    assert db.get_session("long-webui-session")["message_count"] == 100
