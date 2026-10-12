from django.db import migrations

# SQLSTATE 23000 makes Django raise IntegrityError for a rejected statement.
FORWARD = """
CREATE FUNCTION hisobot_reject() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END
$$;

CREATE TRIGGER change_append_only BEFORE UPDATE OR DELETE ON hisobot_change
    FOR EACH ROW EXECUTE FUNCTION hisobot_reject();

CREATE TRIGGER entry_no_delete BEFORE DELETE ON hisobot_entry
    FOR EACH ROW EXECUTE FUNCTION hisobot_reject();

ALTER TABLE hisobot_entry ADD CONSTRAINT entry_period_end_boundary
    CHECK ((period_end AT TIME ZONE 'Asia/Tashkent')::time IN ('08:00', '20:00'));
"""

REVERSE = """
ALTER TABLE hisobot_entry DROP CONSTRAINT entry_period_end_boundary;
DROP TRIGGER entry_no_delete ON hisobot_entry;
DROP TRIGGER change_append_only ON hisobot_change;
DROP FUNCTION hisobot_reject();
"""


class Migration(migrations.Migration):
    dependencies = [("hisobot", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
