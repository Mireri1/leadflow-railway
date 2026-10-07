-- "Callback info" for the decision maker — what the front desk says about
-- reaching them: "in Tue/Thu mornings", "try after 2pm", "ext 204, ask for
-- Mike". Free text, caller-written, shown wherever the lead comes due so the
-- next-day call lands when they are actually at their desk.
--
-- Same contract as dm_phone (009): the frontend writes it in a PATCH separate
-- from the call-outcome save, so a missing column fails only this field and
-- says so — never the call log or the name.
alter table leads add column if not exists reach_notes text;
comment on column leads.reach_notes is
  'How/when to reach the decision maker, as told by the desk (e.g. "Tue/Thu mornings, ext 204"). Caller-written free text.';
