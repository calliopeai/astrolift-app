"""#2220/#169 PII reduction; spec 04 §11 retains the structural user/audit rows.

The existing spec 04 append-only requirement and retention exception remain:
ordinary UPDATE/DELETE are refused. Privacy UPDATE is accepted only when NEW
equals the database's own exact, attributed PII projection of OLD. A session
flag alone never permits arbitrary column or JSON changes.
"""

from importlib import import_module

from django.db import migrations

SQL = r"""
CREATE FUNCTION astrolift_privacy_string_leaves(value jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE result jsonb; entry record;
BEGIN
    IF jsonb_typeof(value) = 'string' THEN RETURN '"[anonymized]"'::jsonb; END IF;
    IF jsonb_typeof(value) = 'object' AND NOT EXISTS (
        SELECT 1 FROM jsonb_object_keys(value) AS key WHERE key NOT IN ('old','new')
    ) THEN
        result := '{}'::jsonb;
        FOR entry IN SELECT * FROM jsonb_each(value) LOOP
            result := result || jsonb_build_object(entry.key, astrolift_privacy_string_leaves(entry.value));
        END LOOP;
        RETURN result;
    END IF;
    RETURN value;
END;
$$;

CREATE FUNCTION astrolift_user_privacy_payload(
    payload jsonb, erased bigint, actor bigint, subject bigint,
    scope_id bigint DEFAULT NULL, accept_id boolean DEFAULT false, nested boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE result jsonb; entry record; child_scope bigint; own_scope bigint;
        own_actor bigint := actor; declared boolean := false; candidate text;
BEGIN
    IF jsonb_typeof(payload) = 'array' THEN
        SELECT coalesce(jsonb_agg(astrolift_user_privacy_payload(
            item, erased, actor, subject, scope_id, accept_id, true)), '[]'::jsonb)
        INTO result FROM jsonb_array_elements(payload) AS item;
        RETURN result;
    END IF;
    IF jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN payload; END IF;
    own_scope := CASE WHEN nested THEN scope_id ELSE coalesce(scope_id, subject) END;
    IF payload ? 'user_id' THEN candidate := payload->>'user_id'; declared := true;
    ELSIF payload ? 'subject_user_id' THEN candidate := payload->>'subject_user_id'; declared := true;
    ELSIF accept_id AND payload ? 'id' THEN candidate := payload->>'id'; declared := true;
    END IF;
    IF declared THEN
        own_scope := CASE WHEN candidate ~ '^[1-9][0-9]{0,17}$' THEN candidate::bigint ELSE NULL END;
        IF (payload ? 'user_id' AND payload ? 'subject_user_id'
            AND payload->>'user_id' IS DISTINCT FROM payload->>'subject_user_id') THEN own_scope := NULL; END IF;
        IF accept_id AND payload ? 'id' AND payload->>'id' IS DISTINCT FROM candidate THEN own_scope := NULL; END IF;
        IF nested AND own_scope IS DISTINCT FROM erased THEN RETURN payload; END IF;
    END IF;
    IF payload ? 'actor_user_id' THEN
        candidate := payload->>'actor_user_id';
        own_actor := CASE WHEN candidate ~ '^[1-9][0-9]{0,17}$' THEN candidate::bigint ELSE NULL END;
    END IF;
    result := '{}'::jsonb;
    FOR entry IN SELECT * FROM jsonb_each(payload) LOOP
        IF (own_scope = erased AND entry.key = ANY(ARRAY[
            'email','name','full_name','first_name','last_name','given_name','family_name',
            'middle_name','display_name','nickname','username','phone','phone_number',
            'avatar_url','picture','address','birth_date','ip_address','user_agent','device_label','geo_hint',
            'subject_email','target_email','user_email'
        ])) OR (own_actor = erased AND entry.key = ANY(ARRAY[
            'actor_email','actor_name','actor_username','request_email','request_ip','request_user_agent'
        ])) THEN
            result := result || jsonb_build_object(entry.key, astrolift_privacy_string_leaves(entry.value));
        ELSE
            child_scope := CASE
                WHEN entry.key IN ('actor','request') THEN own_actor
                WHEN entry.key IN ('user','subject','target','profile','identity','before','after') THEN own_scope
                ELSE NULL END;
            result := result || jsonb_build_object(entry.key, astrolift_user_privacy_payload(
                entry.value, erased, own_actor, subject, child_scope,
                entry.key IN ('actor','user','subject','target'), true));
        END IF;
    END LOOP;
    RETURN result;
END;
$$;

CREATE FUNCTION astrolift_user_privacy_mentions(payload jsonb, erased bigint) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(jsonb_path_exists(payload, '$.** ? (
        @.user_id == $number || @.user_id == $string ||
        @.subject_user_id == $number || @.subject_user_id == $string ||
        @.actor_user_id == $number || @.actor_user_id == $string ||
        @.actor.id == $number || @.actor.id == $string ||
        @.user.id == $number || @.user.id == $string ||
        @.subject.id == $number || @.subject.id == $string ||
        @.target.id == $number || @.target.id == $string
    )', jsonb_build_object('number', erased, 'string', erased::text)), false);
$$;

CREATE OR REPLACE FUNCTION astrolift_refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE erased_text text; erased bigint; expected jsonb; row_actor bigint; row_subject bigint;
BEGIN
    IF TG_OP = 'DELETE' AND coalesce(current_setting('astrolift.retention_sweep', true), '') = 'on'
    THEN RETURN OLD; END IF;
    erased_text := current_setting('astrolift.privacy_user', true);
    IF TG_OP = 'UPDATE' AND erased_text ~ '^[1-9][0-9]{0,17}$'
       AND TG_TABLE_NAME IN ('astrolift_operations_auditevent','astrolift_operations_event') THEN
        erased := erased_text::bigint;
        IF NOT EXISTS (SELECT 1 FROM auth_user WHERE id = erased AND NOT is_active
                       AND right(email, length('@anon-astrolift.net')) = '@anon-astrolift.net') THEN
            RAISE EXCEPTION 'astrolift: privacy target unavailable' USING ERRCODE = '25006';
        END IF;
        IF TG_TABLE_NAME = 'astrolift_operations_auditevent' THEN
            row_actor := CASE WHEN OLD.actor_kind = 'user' AND OLD.actor_id = erased_text THEN erased ELSE NULL END;
            row_subject := CASE WHEN lower(OLD.target_kind) IN ('user','auth_user','usertype')
                                AND OLD.target_id = erased_text THEN erased ELSE NULL END;
            expected := to_jsonb(OLD) || jsonb_build_object(
                'data', astrolift_user_privacy_payload(OLD.data, erased, row_actor, row_subject),
                'reasoning', astrolift_user_privacy_payload(OLD.reasoning, erased, row_actor, row_subject),
                'actor_display', CASE WHEN row_actor = erased AND OLD.actor_display <> '' THEN '[anonymized]' ELSE OLD.actor_display END,
                'request_ip', CASE WHEN row_actor = erased AND OLD.request_ip <> '' THEN '[anonymized]' ELSE OLD.request_ip END,
                'request_user_agent', CASE WHEN row_actor = erased AND OLD.request_user_agent <> '' THEN '[anonymized]' ELSE OLD.request_user_agent END,
                'target_slug', CASE WHEN row_subject = erased AND OLD.target_slug <> '' THEN '[anonymized]' ELSE OLD.target_slug END
            );
        ELSE
            row_actor := OLD.actor_user_id;
            row_subject := CASE WHEN lower(OLD.resource_kind) IN ('user','auth_user','usertype')
                                AND OLD.resource_id = erased_text THEN erased ELSE NULL END;
            expected := to_jsonb(OLD) || jsonb_build_object(
                'payload', astrolift_user_privacy_payload(OLD.payload, erased, row_actor, row_subject)
            );
        END IF;
        IF to_jsonb(NEW) = expected THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'astrolift: % refused on append-only table %.% (only scheduled retention or exact user privacy reduction)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '25006';
END;
$$;

CREATE FUNCTION astrolift_redact_user_history(erased bigint) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE audit_count bigint; event_count bigint;
BEGIN
    IF coalesce(current_setting('astrolift.privacy_user', true), '') <> '' THEN
        RAISE EXCEPTION 'astrolift: privacy projection cannot nest';
    END IF;
    PERFORM id FROM auth_user WHERE id = erased AND NOT is_active
        AND right(email, length('@anon-astrolift.net')) = '@anon-astrolift.net' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'astrolift: privacy target unavailable'; END IF;
    PERFORM set_config('astrolift.privacy_user', erased::text, true);
    WITH projected AS (
        SELECT id,
            astrolift_user_privacy_payload(data, erased,
                CASE WHEN actor_kind = 'user' AND actor_id = erased::text THEN erased ELSE NULL END,
                CASE WHEN lower(target_kind) IN ('user','auth_user','usertype') AND target_id = erased::text THEN erased ELSE NULL END) AS data,
            astrolift_user_privacy_payload(reasoning, erased,
                CASE WHEN actor_kind = 'user' AND actor_id = erased::text THEN erased ELSE NULL END,
                CASE WHEN lower(target_kind) IN ('user','auth_user','usertype') AND target_id = erased::text THEN erased ELSE NULL END) AS reasoning,
            CASE WHEN actor_kind = 'user' AND actor_id = erased::text AND actor_display <> '' THEN '[anonymized]' ELSE actor_display END AS actor_display,
            CASE WHEN actor_kind = 'user' AND actor_id = erased::text AND request_ip <> '' THEN '[anonymized]' ELSE request_ip END AS request_ip,
            CASE WHEN actor_kind = 'user' AND actor_id = erased::text AND request_user_agent <> '' THEN '[anonymized]' ELSE request_user_agent END AS request_user_agent,
            CASE WHEN lower(target_kind) IN ('user','auth_user','usertype') AND target_id = erased::text AND target_slug <> '' THEN '[anonymized]' ELSE target_slug END AS target_slug
        FROM astrolift_operations_auditevent
        WHERE (actor_kind = 'user' AND actor_id = erased::text)
           OR (lower(target_kind) IN ('user','auth_user','usertype') AND target_id = erased::text)
           OR astrolift_user_privacy_mentions(data, erased)
           OR astrolift_user_privacy_mentions(reasoning, erased)
    ) UPDATE astrolift_operations_auditevent AS old SET data = p.data, reasoning = p.reasoning,
        actor_display = p.actor_display, request_ip = p.request_ip,
        request_user_agent = p.request_user_agent, target_slug = p.target_slug
      FROM projected AS p WHERE old.id = p.id AND
        (old.data, old.reasoning, old.actor_display, old.request_ip, old.request_user_agent, old.target_slug)
        IS DISTINCT FROM (p.data, p.reasoning, p.actor_display, p.request_ip, p.request_user_agent, p.target_slug);
    GET DIAGNOSTICS audit_count = ROW_COUNT;
    WITH projected AS (
        SELECT id, astrolift_user_privacy_payload(payload, erased, actor_user_id,
            CASE WHEN lower(resource_kind) IN ('user','auth_user','usertype') AND resource_id = erased::text THEN erased ELSE NULL END) AS payload
        FROM astrolift_operations_event
        WHERE actor_user_id = erased
           OR (lower(resource_kind) IN ('user','auth_user','usertype') AND resource_id = erased::text)
           OR astrolift_user_privacy_mentions(payload, erased)
    ) UPDATE astrolift_operations_event AS old SET payload = p.payload FROM projected AS p
      WHERE old.id = p.id AND old.payload IS DISTINCT FROM p.payload;
    GET DIAGNOSTICS event_count = ROW_COUNT;
    PERFORM set_config('astrolift.privacy_user', '', true);
    RETURN jsonb_build_object('audit_events', audit_count, 'events', event_count);
EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('astrolift.privacy_user', '', true);
    RAISE;
END;
$$;
"""

REVERSE_SQL = (
    import_module("astrolift_operations.migrations.0023_retention_sweep_gate").GATED_TRIGGER_FN
    + """
DROP FUNCTION astrolift_redact_user_history(bigint);
DROP FUNCTION astrolift_user_privacy_mentions(jsonb,bigint);
DROP FUNCTION astrolift_user_privacy_payload(jsonb,bigint,bigint,bigint,bigint,boolean,boolean);
DROP FUNCTION astrolift_privacy_string_leaves(jsonb);
"""
)


class Migration(migrations.Migration):
    dependencies = [("astrolift_operations", "0027_workflowrun_history_expiry")]
    operations = [migrations.RunSQL(SQL, reverse_sql=REVERSE_SQL)]
