"""Reversible migration of retired report channels; messages and blobs are preserved."""


async def migrate_checkpoint_contract(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute("""CREATE TABLE IF NOT EXISTS checkpoint_legacy_contracts (
                thread_id text NOT NULL, checkpoint_ns text NOT NULL,
                checkpoint_id text NOT NULL, checkpoint jsonb NOT NULL,
                metadata jsonb NOT NULL, archived_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id))""")
            await connection.execute("""INSERT INTO checkpoint_legacy_contracts
                (thread_id,checkpoint_ns,checkpoint_id,checkpoint,metadata)
                SELECT thread_id,checkpoint_ns,checkpoint_id,checkpoint,metadata
                FROM checkpoints
                WHERE checkpoint #> '{channel_versions,structured_response}' IS NOT NULL
                   OR checkpoint #> '{channel_values,structured_response}' IS NOT NULL
                ON CONFLICT DO NOTHING""")
            # Retain referenced checkpoint_blobs unchanged for recovery; do not deserialize
            # the removed model or reintroduce a compatibility Pydantic report class.
            await connection.execute("""UPDATE checkpoints SET checkpoint =
                checkpoint #- '{channel_versions,structured_response}'
                           #- '{channel_values,structured_response}'
                WHERE checkpoint #> '{channel_versions,structured_response}' IS NOT NULL
                   OR checkpoint #> '{channel_values,structured_response}' IS NOT NULL""")
            # The previous application already scoped IDs as org__user__conversation.
            # Unscoped IDs have ambiguous ownership and remain preserved, unassigned.
            await connection.execute("""UPDATE checkpoints SET metadata = metadata ||
                jsonb_build_object('org_id',split_part(thread_id,'__',1),
                                   'user_id',split_part(thread_id,'__',2))
                WHERE NOT (metadata ? 'org_id') AND NOT (metadata ? 'user_id')
                  AND split_part(thread_id,'__',1) <> ''
                  AND split_part(thread_id,'__',2) <> ''
                  AND split_part(thread_id,'__',3) <> ''""")
