-- schema.sql

CREATE TABLE IF NOT EXISTS channel_state (
    channel_id          INTEGER PRIMARY KEY,
    guild_id            INTEGER NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'helpful'
                        CHECK (mode IN ('helpful', 'roast')),
    last_indexed_msg_id INTEGER NOT NULL DEFAULT 0,
    updated_at          INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_channel_state_guild 
    ON channel_state(guild_id);

CREATE TABLE IF NOT EXISTS messages (
    message_id          INTEGER PRIMARY KEY,
    channel_id          INTEGER NOT NULL,
    guild_id            INTEGER NOT NULL,
    author_id           INTEGER NOT NULL,
    author_name         TEXT NOT NULL,
    author_display_name TEXT NOT NULL,
    content             TEXT NOT NULL,
    has_attachment      INTEGER NOT NULL DEFAULT 0,
    reply_to_msg_id     INTEGER,
    created_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_time 
    ON messages(channel_id, created_at DESC, message_id DESC);

CREATE INDEX IF NOT EXISTS idx_messages_author_time 
    ON messages(guild_id, author_id, created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    author_name,
    author_display_name,
    content='messages',
    content_rowid='message_id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS trg_messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, author_name, author_display_name)
    VALUES (new.message_id, new.content, new.author_name, new.author_display_name);
END;

CREATE TRIGGER IF NOT EXISTS trg_messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, author_name, author_display_name)
    VALUES ('delete', old.message_id, old.content, old.author_name, old.author_display_name);
END;

CREATE TRIGGER IF NOT EXISTS trg_messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, author_name, author_display_name)
    VALUES ('delete', old.message_id, old.content, old.author_name, old.author_display_name);
    INSERT INTO messages_fts(rowid, content, author_name, author_display_name)
    VALUES (new.message_id, new.content, new.author_name, new.author_display_name);
END;