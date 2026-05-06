"""Deterministic Finite Automaton (DFA) model base.

Provides a state-machine model that can be attached to Django models.
State transitions are defined declaratively. Scheduled transitions
run via Django management commands (or Temporal activities in production).
"""
import contextlib
import dataclasses
import enum
import logging
from typing import Any, Callable, Optional, Type, TypeVar, get_type_hints

import django
from django.apps import AppConfig
from django.db import models

SelfDfaInstanceQueryset = TypeVar("DfaInstanceQueryset", bound="DfaInstanceQueryset")


logger = logging.getLogger(__name__)


class State:

    @property
    def is_initial(self) -> bool:
        return self.state.is_initial

    @property
    def is_final(self) -> bool:
        return self.state.is_final

    @property
    def name(self) -> str:
        return self.function.__name__

    @property
    def schedule(self) -> Optional[dict]:
        return self.state.scheduler_options

    def __init__(self, state: "state", function: Callable[[Any], Any]):
        self.state = state
        self.function = function

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)

    def __hash__(self) -> int:
        return hash(self.name)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'State: {self.name}'


@dataclasses.dataclass
class state:
    is_final: bool = False
    is_initial: bool = False
    scheduler_options: Optional[dict] = None

    def __call__(self, function: Callable[[Any], Any]) -> State:
        return State(self, function)


class DfaInstanceQueryset(models.QuerySet):

    def by_state(self, state: State) -> SelfDfaInstanceQueryset:
        return self.filter(
            state=state.name,
            deferred_until__lte=django.utils.timezone.now()
        )


class DfaBase(models.base.ModelBase):

    def __new__(cls, name, bases, attrs, **kwargs):
        meta = "Meta"
        if meta in attrs and attrs[meta].abstract:
            return models.base.ModelBase.__new__(cls, name, bases, attrs, **kwargs)

        initial_state: State
        states: dict[str, State] = {}
        initial_states: set[State] = set()
        final_states: set[State] = set()
        symbols_class: Type[enum.Enum]
        symbols_classes: set[type] = set()
        transitions: dict[State, dict[enum.Enum, State]] = attrs['transitions']

        for attr_name, attr in attrs.items():
            match attr:
                case State():
                    state_attr: State = attr
                    states[attr_name] = state_attr
                    if state_attr.is_initial:
                        initial_states.add(state_attr)
                    if state_attr.is_final:
                        final_states.add(state_attr)
                    symbols_classes.add(get_type_hints(state_attr.function)['return'])

        assert len(initial_states) == 1, "Initial state must be unique"
        initial_state, = initial_states

        assert len(symbols_classes) == 1, "Symbol class must be unique"
        symbols_class, = symbols_classes

        # self loop for every final state
        for _attr_name, state_attr in states.items():
            if state_attr.is_final:
                transitions[state_attr] = {}
                for symbol in symbols_class:
                    transitions[state_attr][symbol] = state_attr

        attrs["initial_state"] = initial_state
        attrs["states"] = states
        attrs["final_state"] = frozenset(final_states)
        attrs["symbols"] = symbols_class
        attrs["schedulers"] = {}
        attrs["state"] = models.CharField(
            max_length=max([len(state_name) for state_name in states.keys()]),
            choices={state_name: state_name for state_name in states.keys()},
            default=initial_state.name,
        )

        new_class = models.base.ModelBase.__new__(cls, name, bases, attrs, **kwargs)

        return new_class


class DfaInstance(models.Model, metaclass=DfaBase):
    class Meta:
        abstract = True
        ordering = ['deferred_until']

    objects = DfaInstanceQueryset.as_manager()

    initial: State
    final_states: frozenset[State]
    states: dict[str, State]
    symbols: type
    transitions: dict[State, dict[enum.Enum, State]]
    state: models.CharField
    version = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deferred_until = models.DateTimeField(auto_now_add=True, blank=False, null=True)
    retries = models.IntegerField(default=3)

    @contextlib.contextmanager
    def _on_context_(self):
        yield
        self.save()

    def step(self):
        source_state = self.states[self.state]
        with self._on_context_():
            try:
                result = source_state(self)
            except Exception as e:
                logger.exception(f"Exception occurred: {source_state}")
                result = self.symbols(e)
                logger.warning(f'Fallback {e} as {result}')
            target_state = self.transitions[source_state][result]
            self.state = target_state
            logger.info(f'{source_state} with {result} to {target_state}')
        return result

    def save(self, *args, **kwargs):
        self.version = self.version + 1
        super().save(*args, **kwargs)

    @classmethod
    def run_scheduled_states(cls):
        """Run all scheduled states. Call from a management command or Temporal activity."""
        for state_obj in cls.states.values():
            if state_obj.schedule is not None:
                queryset = cls.objects.by_state(state_obj)[0:100]
                for instance in queryset:
                    try:
                        instance.step()
                    except Exception as e:
                        logger.exception(f'DFA step failed for {instance}: {e}')

    @classmethod
    def post_migration(cls, app_config: AppConfig) -> None:
        """Hook for post-migration setup. Override in subclasses if needed."""
        pass
