# SPDX-License-Identifier: MPL-2.0
"""Source ordering and input-loss choices shared by external controls."""

ACTUATOR_PROTOCOLS = {"bow_thruster": "TRC", "engine": "PRC"}
RUDDER_SOURCES = ("ROR", "RSA", "XDR_ORDER", "XDR_RESPONSE")
INPUT_LOSS_MODES = ("MANUAL", "FREEZE", "ZERO")


def ordered_sources(policy: str, choices: tuple[str, ...]) -> tuple[str, ...]:
    if policy == "MANUAL":
        return ()
    if policy in choices:
        return (policy,)
    for main in choices:
        for fallback in choices:
            if main != fallback and policy == f"PRIO_{main}_{fallback}":
                return main, fallback
    raise ValueError(f"Invalid external control source: {policy}")


def source_policy(main: str, fallback: str, choices: tuple[str, ...]) -> str:
    policy = f"PRIO_{main}_{fallback}" if fallback else main
    ordered_sources(policy, choices)
    return policy


def input_loss_mode(value: str) -> str:
    if value not in INPUT_LOSS_MODES:
        raise ValueError("On input loss must be Manual, Freeze or Zero")
    return value


def actuator_sources(actuator: str, policy: str) -> tuple[str, ...]:
    if actuator not in ACTUATOR_PROTOCOLS:
        raise ValueError("Actuator must be bow_thruster or engine")
    return ordered_sources(policy, (ACTUATOR_PROTOCOLS[actuator], "XDR"))
