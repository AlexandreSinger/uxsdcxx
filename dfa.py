# Our approach to model groups is:
# * Build an NFA out of them.
# * Translate our NFA to automata-lib's NFA.
# * Using automata-lib, convert the NFA to a DFA.
# * Using python-automata, minify the DFA.
# * Rename the DFA states. Map trap states to None.
# * Return the DFA, where it would be emitted as C++ code.

import xmlschema
from xmlschema.validators import (
    XsdElement,
    XsdGroup
)

from automata.fa.dfa import DFA
from automata.fa.nfa import NFA
from third_party.DFA import DFA as pDFA

from itertools import permutations
from typing import List, Tuple, Dict, Set, Union

def dfa_from_group(t: XsdGroup):
	# Fill in a NFA of automata-lib type.
	_nfa_states: Set[str] = set()
	_nfa_state_transitions: Dict[str, Dict[str, Set[Union[str, None]]]] = {}
	_elements: Dict[str, XsdElement] = {}

	def _new_state() -> str:
		x = "q%d" % len(_nfa_states)
		_nfa_states.add(x)
		return x

	def _remove_state(x: str):
		_nfa_states.remove(x)
		_nfa_state_transitions.pop(x)

	def _add_transition(state: str, input: str, next: Union[str, None]):
		if _nfa_state_transitions.get(state) is None:
			_nfa_state_transitions[state] = {input: {next}}
		else:
			if _nfa_state_transitions[state].get(input) is None:
				_nfa_state_transitions[state][input] = {next}
			else:
				_nfa_state_transitions[state][input].add(next)

	def _patch(state: str, next: str):
		for k, v in _nfa_state_transitions[state].items():
			_nfa_state_transitions[state][k] = {next if x is None else x for x in v}

	# start --a-->
	def _nfa_from_element(t: XsdElement) -> Tuple[str, Set[str]]:
		x = _new_state()
		_add_transition(x, t.name, None)
		_elements[t.name] = t
		return (x, {x})

	# start --a-> O --b--> O --c-->
	def _nfa_from_sequence(t: XsdGroup) -> Tuple[str, Set[str]]:
		init, vacant = _nfa_from_node(t._group[0])
		for e in t._group[1:]:
			init2, vacant2 = _nfa_from_node(e)
			for v in vacant:
				_patch(v, init2)
			vacant = vacant2
		return (init, vacant)

	# |-----a->
	# start --b->
	# |-----c->
	def _nfa_from_choice(t: XsdGroup) -> Tuple[str, Set[str]]:
		x = _new_state()
		vacants = set()
		for e in t._group:
			init, vacant = _nfa_from_node(e)
			_add_transition(x, "", init)
			vacants |= vacant
		return (x, vacants)

	# |-----a-> O --b--> O --c-->
	# start --a-> O --c--> O --b-->
	# |-----b-> ...
	# This grows factorially! Be careful when using xs:all for many elements.
	def _nfa_from_all(t: XsdGroup) -> Tuple[str, Set[str]]:
		original_group = t._group
		x = _new_state()
		vacants = set()
		for p in permutations(t._group):
			t._group = list(p)
			init, vacant = _nfa_from_sequence(t)
			_add_transition(x, "", init)
			vacants |= vacant
		t._group = original_group
		return (x, vacants)

	# Generate a NFA from a model group or element.
	# Return start state and "before-final" states which include transitions
	# to None which denote vacant out-going arrows.
	def _nfa_from_node(t: Union[XsdElement, XsdGroup]) -> Tuple[str, Set[str]]:
		if isinstance(t, XsdElement):
			init, vacant = _nfa_from_element(t)
		elif t.model == "sequence":
			init, vacant = _nfa_from_sequence(t)
		elif t.model == "choice":
			init, vacant = _nfa_from_choice(t)
		elif t.model == "all":
			init, vacant = _nfa_from_all(t)
		else:
			raise NotImplementedError("I don't know what to do with model group node %s." % t)

		if t.occurs == [1, 1]:
			return (init, vacant)
		elif t.occurs == [0, 1]:
			_add_transition(init, "", None)
			vacant.add(init)
			return (init, vacant)
		elif t.occurs == [0, None]:
			for v in vacant:
				_patch(v, init)
			_add_transition(init, "", None)
			return (init, {init})
		elif t.occurs == [1, None]:
			next = _new_state()
			for v in vacant:
				_patch(v, next)
			_add_transition(next, "", init)
			_add_transition(next, "", None)
			return (init, {next})
		else:
			raise NotImplementedError("(min_occurs, max_occurs) pair %s is not supported" % t.occurs)

	init, finals = _nfa_from_node(t)
	final = _new_state()
	_nfa_state_transitions[final] = {}
	for f in finals:
		_patch(f, final)

	input_symbols = set()
	for v in _nfa_state_transitions.values():
		input_symbols |= set(v.keys())
	input_symbols.remove("")

	nfa = NFA(states=_nfa_states,
			input_symbols=input_symbols,
			transitions=_nfa_state_transitions,
			initial_state=init,
			final_states={final})
	dfa = DFA.from_nfa(nfa)
	pdfa = pDFA(dfa.states,
			dfa.input_symbols,
			lambda q,c: dfa.transitions[q][c],
			dfa.initial_state,
			dfa.final_states)
	pdfa.minimize()

	# "{}" comes from automata-lib and means trap state.
	# We will filter transitions to the trap state out and emit
	# an error message if a state transition is not found for the given input.
	state_map = {k: v for v, k in enumerate(pdfa.states)}
	out = {}
	out["states"] = {state_map[x] for x in pdfa.states if x != "{}"}
	out["start"] = state_map[pdfa.start]
	out["accepts"] = {state_map[x] for x in pdfa.accepts if x != "{}"}
	out["transitions"] = {state_map[q]: {k: state_map[pdfa.delta(q, k)] for k in pdfa.alphabet if pdfa.delta(q, k) != "{}"} for q in pdfa.states if q != "{}"}
	out["elements"] = _elements

	return out
