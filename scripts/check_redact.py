"""Offline checks for personal-data masking and the storage boundary."""
import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ['FAKE'] = '1'
os.environ['SIM_WORD_S'] = '0'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guard.brain import classify
from guard.brain import redact as privacy
from guard import flow, settings, sim, state


def checks():
    cases = [
        ('Call +1 (555) 010-0123', 'phone number'),
        ('Card 4111 1111 1111 1111', 'card number'),
        ('SSN 000-00-0000', 'social security number'),
        ('Email demo@example.test', 'email address'),
        ('Your code is 123456', 'one-time code'),
        ('PIN: 654321', 'one-time code'),
        ('IBAN GB00 TEST 0000 0000 0000 00', 'IBAN'),
        ('bank account number is 000123456789', 'bank account number'),
        ('routing number 000000000', 'routing number'),
        ('password is pretend-secret', 'password'),
        ('I live at 123 Demo Street', 'address'),
        ('date of birth 00/00/0000', 'date of birth'),
    ]
    for text, kind in cases:
        result = privacy.redact(text)
        assert f'[{kind}]' in result.text, (text, result)
        assert kind in result.kinds
    readable = "Jakob, your son from FedEx, needs $2,000 in gift cards or a wire, Mum."
    assert privacy.redact(readable).text == readable

    with patch.object(classify, '_enabled', return_value=True), patch.object(classify, '_post', side_effect=RuntimeError('offline')):
        result = privacy.redact('Card 4111 1111 1111 1111')
        assert result.text == 'Card [card number]' and result.source == 'rules'

    assert privacy.redact('IBAN GB00 TEST 0000 0000 0000 00 Jakob FedEx').text == 'IBAN [IBAN] Jakob FedEx'

    payloads = []
    def engine(model, body, timeout):
        payloads.append(body)
        return {'items': [{'entities': [{'label': 'password', 'text': 'hiddenphrase'}]}]}
    with patch.object(classify, '_enabled', return_value=True), patch.object(classify, '_request', side_effect=engine):
        result = privacy.redact('Say hiddenphrase or email demo@example.test')
        assert result.text == 'Say [password] or email [email address]'
        assert result.source == 'sie'
        assert 'password' in payloads[0]['params']['labels']
        payloads.clear()
        # Engine is off by default: no call for any event type.
        state.emit('engine-test', 'transcript', {'role': 'caller', 'text': 'Say hiddenphrase'})
        assert not payloads
        with patch.object(settings, 'REDACT_SIE', True):
            state.emit('engine-test', 'claim', {'quote': 'Say hiddenphrase'})
            state.emit('engine-test', 'transcript_partial', {'text': 'Say hiddenphrase'})
            assert not payloads
            event = state.emit('engine-test', 'transcript', {'role': 'caller', 'text': 'Say hiddenphrase', 'x': 'Jakob'})
            assert event.data['text'] == 'Say [password]'
            assert event.data['x'] == 'Jakob'
            assert len(payloads) == 1
    with patch.object(classify, '_enabled', return_value=True), patch.object(classify, '_request', return_value={
        'items': [{'entities': [{'label': 'address', 'text': 'Demo building, demo@example.test'}]}]
    }):
        assert privacy.redact('At Demo building, demo@example.test').text == 'At [address]'

    def slow(*args):
        time.sleep(.6)
        raise RuntimeError('offline')
    with patch.object(classify, '_enabled', return_value=True), patch.object(classify, '_post', side_effect=slow):
        start = time.perf_counter()
        assert privacy.redact('code 123456').text == 'code [one-time code]'
        assert time.perf_counter() - start < .5

    finals = ["Mum, it's me, I need $2000 in gift cards now, don't tell dad", 'This is Jacob',
              'I need 2000 dollars', 'two thousand dollars',
              'Hi, this is FedEx, I have a package for delivery at 3 pm',
              'there is a customs fee of $4.99, I need your card number']
    for line in finals:
        assert privacy.redact(line, rules_only=True).text == line, line
        words = line.split()
        for i in range(1, len(words) + 1):
            part = ' '.join(words[:i])
            assert privacy.redact(part, rules_only=True, partial=True).text == part, part
    assert privacy.redact("it's 4111 1111 1111 1111", rules_only=True).text == "it's [card number]"
    assert '4111' not in privacy.redact("it's 4111 1111 1111", rules_only=True, partial=True).text
    assert privacy.redact('call me back on 555 0100', rules_only=True).text == 'call me back on [phone number]'
    assert privacy.redact('my number is 555 0100', rules_only=True).text == 'my number is [phone number]'
    assert privacy.redact('ref 123456 is ready', rules_only=True, partial=True).text == 'ref [number] is ready'

    import re
    for line in ['My card number is 4111 1111 1111 1111.', "sure, it's 4111 1111 1111 1111", 'call me back on 555 0100']:
        words = line.split()
        cid = flow.start_call(None)
        flow.mark_live(cid)
        for role in ('caller', 'agent'):
            for i in range(1, len(words) + 1):
                part = ' '.join(words[:i])
                shown = privacy.redact(part, rules_only=True, partial=True).text
                assert not re.search(r'\d\d', shown), (part, shown)
                flow.on_partial(cid, role, part)
                live_text = (flow.live()[role] or {}).get('text', '')
                assert not re.search(r'\d\d', live_text), (part, live_text)
    for line in ['I need 2000 dollars', 'I need $2,000 in gift cards right now.',
                 'there is a small customs fee of $4.99 to pay', 'delivery at 3 pm']:
        words = line.split()
        for i in range(1, len(words) + 1):
            part = ' '.join(words[:i])
            assert privacy.redact(part, rules_only=True, partial=True).text == part, part

    async def integration():
        state.backend = state.MemoryBackend()
        line = "This is Jakob, your son. Card 4111 1111 1111 1111, send $2,000 in gift cards."
        original_flags = classify.flags
        seen = []
        claims = []
        original_claim = classify.claim
        def capture_claim(text):
            claims.append(text)
            return original_claim(text)
        def capture(text):
            seen.append(text)
            return original_flags(text)
        call_id = flow.start_call(None)
        with patch.object(classify, 'flags', side_effect=capture), patch.object(classify, 'claim', side_effect=capture_claim):
            await sim._play(call_id, sim.SimCall(lines=[line], word_s=0))
        assert seen == [line], seen
        assert claims == [line], claims
        events = [e for e in state.events_since(0) if e.call_id == call_id]
        transcript = next(e for e in events if e.type == 'transcript')
        assert '[card number]' in transcript.data['text']
        assert transcript.data['redacted'] == ['card number']
        claim = next(e for e in events if e.type == 'claim')
        assert '[card number]' in claim.data['quote']
        assert claim.data['redacted'] == ['card number']
        assert '4111' not in str([e.model_dump() for e in events])
        nested = {'messages': [{'content': 'Email demo@example.test'}]}
        event = state.emit(call_id, 'conversation', nested)
        assert event.data['messages'][0]['content'] == 'Email [email address]'
        assert nested['messages'][0]['content'] == 'Email demo@example.test'
        assert 'redacted' not in state.emit(call_id, 'action', {'text': 'Hello Jakob'}).data
        await sim._say(call_id, 'agent', 'Card 4111 1111 1111 1111', .001)
        assert '4111' not in str(flow.live())
        # Observe an actual in-progress line, before its final clears the live buffer.
        task = asyncio.create_task(sim._say(call_id, 'agent', 'Card 4111 1111 1111 1111', .02))
        await asyncio.sleep(.055)
        assert '4111' not in str(flow.live())
        await task
        # A late partial of a line that just became final is dropped, digits or not.
        cid = flow.start_call(None)
        flow.mark_live(cid)
        await flow.on_transcript(cid, 'agent', 'I need 2000 dollars')
        flow.on_partial(cid, 'agent', 'I need 2000 dollars')
        assert flow.live()['agent'] is None, flow.live()
        flow.on_partial(cid, 'agent', 'Card 4111 1111 1111 1111')
        assert '4111' not in str(flow.live())
        for task in tuple(flow._tasks):
            task.cancel()
    asyncio.run(integration())
    print('OK redaction: rules, engine fallback/budget, original classifier input, simulated storage and partials')


if __name__ == '__main__':
    checks()
