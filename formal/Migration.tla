---------------------------- MODULE Migration ----------------------------
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Reports, Legacy, AllowForeign, ReplayUnknown, SkipValidation
Directions == {"go", "back"}
Ops == Reports \X Directions
Bindings == {"old", "new", "foreign", "unread"}
Target(d) == IF d = "go" THEN "new" ELSE "old"
Origin(d) == IF d = "go" THEN "old" ELSE "new"
EmptyOp == [journal |-> "none", remote |-> "none", sends |-> 0]
Success(o) == o.journal \in {"ok", "reconciled"}
Resolved(o) == o.journal \in {"ok", "failed", "reconciled"}

VARIABLES phase, binding, observed, op, validated, access,
          rollbackStarted, badCompletion, badRollback, badDirection,
          badGate, badForeign
vars == <<phase, binding, observed, op, validated, access,
          rollbackStarted, badCompletion, badRollback, badDirection,
          badGate, badForeign>>
Attempted(r) == op[<<r, "go">>].journal # "none"

Init ==
    /\ phase = "Apply"
    /\ binding = [r \in Reports |-> "old"]
    /\ observed = [r \in Reports |-> "unread"]
    /\ op = [k \in Ops |-> EmptyOp]
    /\ validated = FALSE /\ access = FALSE /\ rollbackStarted = FALSE
    /\ badCompletion = FALSE /\ badRollback = FALSE
    /\ badDirection = FALSE /\ badGate = FALSE /\ badForeign = FALSE

Validate == /\ ~validated /\ validated' = TRUE
            /\ UNCHANGED <<phase, binding, observed, op, access,
                 rollbackStarted, badCompletion, badRollback, badDirection,
                 badGate, badForeign>>
ApproveAccess == /\ ~access /\ access' = TRUE
                 /\ UNCHANGED <<phase, binding, observed, op, validated,
                      rollbackStarted, badCompletion, badRollback, badDirection,
                      badGate, badForeign>>

Observe(r) ==
    /\ phase \in {"Apply", "Rollback"}
    /\ observed' = [observed EXCEPT ![r] = binding[r]]
    /\ UNCHANGED <<phase, binding, op, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

\* One action abstracts durable intent followed by one transmission. The
\* crash-before-send branch creates an intent but transmits nothing. No
\* continuation may resend that intent. Requests and receipts are separate.
Admit(r, d, transmit) ==
    LET k == <<r, d>> IN
    /\ IF d = "go" THEN
           /\ phase = "Apply"
           /\ (validated /\ access) \/ SkipValidation
           /\ ~rollbackStarted \/ Legacy
       ELSE /\ phase = "Rollback" /\ Attempted(r)
    /\ observed[r] = Origin(d)
    /\ op[k].journal = "none" \/
         (ReplayUnknown /\ op[k].journal = "intent" /\ op[k].sends = 1)
    /\ op' = [op EXCEPT ![k] =
         [journal |-> "intent", remote |-> IF transmit THEN "pending" ELSE "none",
          sends |-> @.sends + IF transmit THEN 1 ELSE 0]]
    /\ observed' = [observed EXCEPT ![r] = "unread"]
    /\ badDirection' = (badDirection \/ (d = "go" /\ rollbackStarted))
    /\ badGate' = (badGate \/ (d = "go" /\ ~(validated /\ access)))
    /\ UNCHANGED <<phase, binding, validated, access, rollbackStarted,
         badCompletion, badRollback, badForeign>>

ServerApply(r, d) ==
    LET k == <<r, d>> IN
    /\ op[k].remote = "pending"
    /\ op' = [op EXCEPT ![k].remote = "applied"]
    /\ binding' = [binding EXCEPT ![r] = Target(d)]
    /\ badForeign' = (badForeign \/ (binding[r] = "foreign"))
    /\ UNCHANGED <<phase, observed, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate>>

ServerReject(r, d) ==
    LET k == <<r, d>> IN
    /\ op[k].remote = "pending"
    /\ op' = [op EXCEPT ![k].remote = "rejected"]
    /\ UNCHANGED <<phase, binding, observed, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

Receipt(r, d) ==
    LET k == <<r, d>> IN
    /\ op[k].journal = "intent"
    /\ op[k].remote \in {"applied", "rejected"}
    /\ op' = [op EXCEPT ![k].journal =
         IF op[k].remote = "applied" THEN "ok" ELSE "failed"]
    /\ UNCHANGED <<phase, binding, observed, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

\* Assumption: one linearizable server effect per submitted operation, no
\* foreign writer in the positive model. Then observing the target of an
\* unknown request resolves its effect; observing the origin proves nothing.
Reconcile(r, d) ==
    LET k == <<r, d>> IN
    /\ op[k].journal = "intent"
    /\ binding[r] = Target(d)
    /\ op' = [op EXCEPT ![k].journal = "reconciled"]
    /\ UNCHANGED <<phase, binding, observed, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

StartRollback ==
    /\ phase \in {"Apply", "Done"}
    /\ Legacy \/ \A r \in Reports :
         ~Attempted(r) \/ Resolved(op[<<r, "go">>])
    /\ phase' = "Rollback" /\ rollbackStarted' = TRUE
    /\ observed' = [r \in Reports |-> "unread"]
    /\ UNCHANGED <<binding, op, validated, access, badCompletion, badRollback,
         badDirection, badGate, badForeign>>

\* Old implementation had no durable direction fence until rollback ended.
ResumeApply ==
    /\ Legacy /\ phase = "Rollback"
    /\ \E r \in Reports : op[<<r, "back">>].journal # "none"
    /\ phase' = "Apply"
    /\ observed' = [r \in Reports |-> "unread"]
    /\ UNCHANGED <<binding, op, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

FinishApply ==
    /\ phase = "Apply"
    /\ \A r \in Reports :
         /\ observed[r] = "new" /\ Attempted(r)
         /\ Legacy \/ Success(op[<<r, "go">>])
    /\ phase' = "Done"
    /\ badCompletion' = (badCompletion \/
         (\E r \in Reports :
             binding[r] # "new" \/ ~Success(op[<<r, "go">>])))
    /\ UNCHANGED <<binding, observed, op, validated, access, rollbackStarted,
         badRollback, badDirection, badGate, badForeign>>

FinishRollback ==
    /\ phase = "Rollback"
    /\ \A r \in Reports : ~Attempted(r) \/
         /\ observed[r] = "old"
         /\ Resolved(op[<<r, "go">>])
         /\ Legacy \/ op[<<r, "back">>].journal = "none" \/
              Resolved(op[<<r, "back">>])
    /\ phase' = "Restored"
    /\ badRollback' = (badRollback \/
         (\E r \in Reports : Attempted(r) /\
           (binding[r] # "old" \/ ~Resolved(op[<<r, "go">>]) \/
             (op[<<r, "back">>].journal # "none" /\
               ~Resolved(op[<<r, "back">>])))))
    /\ UNCHANGED <<binding, observed, op, validated, access, rollbackStarted,
         badCompletion, badDirection, badGate, badForeign>>

ForeignChange(r) ==
    /\ AllowForeign /\ binding[r] # "foreign"
    /\ binding' = [binding EXCEPT ![r] = "foreign"]
    /\ UNCHANGED <<phase, observed, op, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

Crash ==
    /\ observed' = [r \in Reports |-> "unread"]
    /\ UNCHANGED <<phase, binding, op, validated, access, rollbackStarted,
         badCompletion, badRollback, badDirection, badGate, badForeign>>

Next == Validate \/ ApproveAccess \/ StartRollback \/ ResumeApply \/
        FinishApply \/ FinishRollback \/ Crash \/
        (\E r \in Reports : Observe(r) \/ ForeignChange(r) \/
           (\E d \in Directions : Admit(r, d, TRUE) \/ Admit(r, d, FALSE) \/
               ServerApply(r, d) \/ ServerReject(r, d) \/
               Receipt(r, d) \/ Reconcile(r, d)))

Spec == Init /\ [][Next]_vars
TypeOK ==
    /\ phase \in {"Apply", "Rollback", "Done", "Restored"}
    /\ binding \in [Reports -> {"old", "new", "foreign"}]
    /\ observed \in [Reports -> Bindings]
    /\ op \in [Ops -> [journal : {"none", "intent", "ok", "failed", "reconciled"},
                          remote : {"none", "pending", "applied", "rejected"},
                          sends : 0..2]]
    /\ <<validated, access, rollbackStarted, badCompletion, badRollback,
          badDirection, badGate, badForeign>> \in [1..8 -> BOOLEAN]
AtMostOnce == \A k \in Ops : op[k].sends <= 1
CutoverGated == ~badGate
NoFalseCompletion == ~badCompletion
NoFalseRollback == ~badRollback
RollbackFence == ~badDirection
NoForeignOverwrite == ~badForeign
NeverComplete == phase # "Done"
NeverRollback == ~(phase = "Restored" /\
                   (\E r \in Reports : Success(op[<<r, "back">>])))
Symmetry == Permutations(Reports)
=============================================================================
