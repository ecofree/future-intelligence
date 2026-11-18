"""One bounded supervisor per pre-registered identity; PostgreSQL remains truth."""
from __future__ import annotations
from datetime import datetime, timezone
import time


def alerts(status: dict) -> list[str]:
    result=[]
    if status.get('reason'):
        result.append(status['reason'])
    if status.get('held_unknown',0):
        result.append('COST_RECONCILIATION_REQUIRED')
    consumed=status.get('spent_microusd',0)+status.get('reserved_microusd',0)
    budget=status.get('budget_microusd',0)
    if budget and consumed*5 >= budget*4:
        result.append('BUDGET_NEAR_LIMIT' if consumed<=budget else 'BUDGET_OVERRUN')
    return result


def operator_tick(repo, tasks, experiment: str, *, now=None) -> dict:
    # Recovery never authorizes another send for an uncertain external call.
    recovered=tasks.recover(experiment)
    state=repo.one('SELECT fi.operation_status(%s::uuid)',(experiment,))
    if not state:
        raise ValueError('unknown experiment')
    moment=now or datetime.now(timezone.utc)
    stop=state.get('dispatch_stop_at')
    result={'status':'OBSERVED','recovered':recovered,'alerts':alerts(state),
            'production_acceptance':'NOT_IMPLIED'}
    if state['mode']=='FORMAL' and stop and moment>=datetime.fromisoformat(stop.replace('Z','+00:00')):
        if not state['registration_batch']:
            return {**result,'status':'BLOCKED','reason':'NO_REGISTRATION_BATCH'}
        bid=repo.capture(experiment,'FORECAST')
        proof=repo.seal(bid)
        return {**result,'status':'FORECAST_SEALED','batch_id':bid,'subject_sha256':proof['subject_sha256'],
                'external_timestamp':'REQUIRES_SEPARATE_ANCHOR_AND_VERIFIER'}
    return result


def loop(step, *, cycles: int, interval: float, emit, stop=lambda:False, sleep=time.sleep):
    if type(cycles) is not int or not 1<=cycles<=10000 or not 1<=interval<=300:
        raise ValueError('bounded service settings required')
    for index in range(cycles):
        if stop():
            emit({'status':'STOP_REQUESTED'});return 0
        try:
            result=step()
        except Exception:
            emit({'status':'STOPPED_UNCERTAIN','automatic_redispatch':False});return 2
        emit(result)
        # The worker has already recorded the failed attempt and unknown cost.
        # Rejected credentials must stop this supervisor before its next claim.
        if result.get('http_status') == 401:
            return 2
        if result.get('status') in {'BLOCKED','NO_REDISPATCH','STOPPED_UNCERTAIN'}:
            return 2
        if index+1<cycles:
            sleep(interval)
    return 0
