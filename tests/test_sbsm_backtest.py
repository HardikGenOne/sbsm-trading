from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from backtesting.sbsm_intraday import Config, calculate_signals, load_csv, pine_rma, pine_supertrend, simulate


def bars(times, opens=None, closes=None, buys=(), sells=(), cutoff='15:15'):
    index = pd.DatetimeIndex(times).tz_localize('Asia/Kolkata')
    opens = opens or [100.0]*len(index)
    closes = closes or opens
    df = pd.DataFrame({'open':opens,'close':closes},index=index)
    df['high'] = df[['open','close']].max(axis=1)+1
    df['low'] = df[['open','close']].min(axis=1)-1
    df['cutoff'] = [pd.Timestamp(str(t.date())+' '+cutoff,tz='Asia/Kolkata') for t in index]
    df['session_end'] = [pd.Timestamp(str(t.date())+' 15:30',tz='Asia/Kolkata') for t in index]
    df['buy_signal'] = [i in buys for i in range(len(index))]
    df['sell_signal'] = [i in sells for i in range(len(index))]
    return df


def test_wilder_seed_is_sma_not_ema_from_first_sample():
    actual = pine_rma(np.array([2.,4.,6.,10.,2.]),3)
    np.testing.assert_allclose(actual,[np.nan,np.nan,4.,6.,14/3],equal_nan=True)


def test_supertrend_seed_flip_and_direction_sign():
    h=np.array([11.,12.,13.,14.,7.]);l=h-2;c=h-1
    atr,st,direction=pine_supertrend(h,l,c,2,0.5)
    np.testing.assert_allclose(atr,[np.nan,2.,2.,2.,5.],equal_nan=True)
    # A close equal to the upper band does NOT flip; only a strict exceedance flips.
    assert list(direction)==[1,1,1,-1,1]
    np.testing.assert_allclose(st[1:],[12.,12.,12.,8.5])


def test_original_breakout_indexing_and_wma_weights():
    df=bars(pd.date_range('2026-11-23 09:15',periods=6,freq='min').strftime('%Y-%m-%d %H:%M').tolist(),opens=[10,11,12,13,14,15])
    out=calculate_signals(df,Config(lookback=3,wma_period=3,atr_period=2,noise_bars=0))
    assert out.previous_breakout_high.iloc[4]==max(df.high.iloc[0:3])
    assert out.previous_breakout_high.iloc[4]!=df.high.iloc[3]
    assert out.wma.iloc[2]==pytest.approx((10+2*11+3*12)/6)
    assert pd.isna(out.wma.iloc[1])


def test_future_prices_cannot_change_past_indicators_or_signals():
    index=pd.date_range('2026-11-23 09:15',periods=100,freq='min')
    prices=(100+np.sin(np.arange(100)/3)*8+np.arange(100)/10).tolist()
    df=bars(index.strftime('%Y-%m-%d %H:%M').tolist(),opens=prices)
    cfg=Config(atr_period=3,wma_period=4,lookback=2,noise_bars=2,st_factor=1)
    before=calculate_signals(df.iloc[:70],cfg)
    changed=df.copy();changed.loc[changed.index[70:],['open','high','low','close']]*=10
    after=calculate_signals(changed,cfg).iloc[:70]
    pd.testing.assert_frame_equal(before,after)


def test_next_open_fills_and_exact_1515_square_off_not_1515_close():
    df=bars(['2026-11-23 15:12','2026-11-23 15:13','2026-11-23 15:14','2026-11-23 15:15'],opens=[100,102,103,900],closes=[101,103,105,999],buys=(0,))
    trades,_,_,position,_=simulate(df,Config())
    t=trades.iloc[0]
    assert t.entry_price==102 and str(t.entry_time).startswith('2026-11-23 15:13')
    assert t.exit_price==105 and str(t.exit_time).startswith('2026-11-23 15:15')
    assert t.exit_reason=='SQUARE_OFF_1515' and position is None


def test_signal_exit_fills_next_bar_open():
    df=bars(['2026-11-23 10:00','2026-11-23 10:01','2026-11-23 10:02'],opens=[100,101,105],buys=(0,),sells=(1,))
    trades,_,_,position,_=simulate(df,Config())
    assert trades.iloc[0].entry_price==101 and trades.iloc[0].exit_price==105
    assert trades.iloc[0].exit_reason=='SBSM_EXIT' and position is None


def test_buy_at_cutoff_is_blocked_and_not_delayed_until_next_day():
    df=bars(['2026-11-23 15:14','2026-11-23 15:15','2026-11-24 09:15'],buys=(0,1))
    trades,events,_,position,pending=simulate(df,Config())
    assert len(trades)==0 and position is None and pending is None
    assert sum(events.action=='BUY_BLOCKED')==2


def test_truncated_current_day_keeps_open_trade_and_last_signal_pending():
    df=bars(['2026-11-23 10:00','2026-11-23 10:01'],opens=[100,101],buys=(0,))
    trades,_,equity,position,_=simulate(df,Config())
    assert len(trades)==0 and position is not None and equity.iloc[-1].long_open
    trades,_,_,position,pending=simulate(df.iloc[:1],Config())
    assert position is None and pending[0]=='BUY'


def test_missing_squareoff_bar_is_not_filled_using_future_or_yesterday_price():
    df=bars(['2026-11-23 15:12','2026-11-23 15:13','2026-11-24 09:15'],buys=(0,))
    with pytest.raises(ValueError,match='Missing square-off'):
        simulate(df,Config())


def test_early_known_session_closes_before_1515():
    df=bars(['2025-10-21 14:42','2025-10-21 14:43','2025-10-21 14:44'],opens=[100,101,102],closes=[100,102,103],buys=(0,),cutoff='14:45')
    trades,_,_,position,_=simulate(df,Config())
    assert trades.iloc[0].exit_reason=='EARLY_SESSION_CLOSE' and position is None
    assert str(trades.iloc[0].exit_time).startswith('2025-10-21 14:45')


def test_costs_and_slippage_are_charged_on_both_sides():
    df=bars(['2026-11-23 10:00','2026-11-23 10:01','2026-11-23 10:02'],opens=[100,100,110],buys=(0,),sells=(1,))
    t=simulate(df,Config(quantity=2,point_value=3,commission_bps=10,slippage_points=1))[0].iloc[0]
    assert t.entry_price==101 and t.exit_price==109
    assert t.gross_pnl==48 and t.commission==pytest.approx(1.26) and t.net_pnl==pytest.approx(46.74)


def test_loader_removes_postclose_and_preserves_special_sessions(tmp_path):
    p=tmp_path/'input.csv'
    pd.DataFrame({'datetime_ist':['2026-11-23T15:29:00+05:30','2026-11-23T15:30:00+05:30','2024-11-01T18:00:00+05:30'], 'open':[100]*3,'high':[101]*3,'low':[99]*3,'close':[100]*3}).to_csv(p,index=False)
    data,quality=load_csv(p,Config())
    assert len(data)==2 and quality['excluded_outside_session_rows']==1
    assert data.index[0].hour==18


def test_flat_sell_updates_cooldown_and_daily_state_is_not_reset():
    df=bars(['2026-11-23 15:12','2026-11-23 15:13','2026-11-23 15:14','2026-11-24 09:15','2026-11-24 09:16'], opens=[100,90,80,70,60])
    out=calculate_signals(df,Config(lookback=1,atr_period=1,wma_period=1,noise_bars=0))
    assert out.flat_sell_state_update.iloc[2]
    assert out.trend_state.iloc[2]==-1 and out.last_signal_bar.iloc[2]==2
    assert out.trend_state.iloc[3]==-1 and out.last_signal_bar.iloc[3]==2
    assert not out.sell_signal.any()


def test_no_squareoff_holds_overnight_and_exits_only_on_signal():
    df=bars(['2026-11-23 15:13','2026-11-23 15:14','2026-11-23 15:15','2026-11-24 09:15','2026-11-24 09:16'],opens=[100,101,102,110,111],buys=(0,),sells=(3,))
    trades,_,_,position,_=simulate(df,Config(force_square_off=False))
    assert len(trades)==1 and position is None
    assert trades.iloc[0].entry_price==101 and trades.iloc[0].exit_price==111
    assert trades.iloc[0].exit_reason=='SBSM_EXIT'
    assert trades.iloc[0].entry_time.date()!=trades.iloc[0].exit_time.date()


def test_no_squareoff_allows_late_signal_to_fill_next_session_open():
    df=bars(['2026-11-23 15:29','2026-11-24 09:15'],opens=[100,120],buys=(0,))
    trades,events,_,position,pending=simulate(df,Config(force_square_off=False,entry_cutoff=False))
    assert len(trades)==0 and pending is None
    assert position['entry_price']==120 and position['entry_time'].day==24
    assert 'BUY_BLOCKED' not in set(events.action)


def test_period_execution_starts_flat_but_preserves_indicator_warmup(tmp_path):
    from backtesting.sbsm_intraday import run
    index=pd.date_range('2026-11-23 09:15',periods=12,freq='min',tz='Asia/Kolkata')
    p=tmp_path/'prices.csv'
    pd.DataFrame({'datetime_ist':index,'open':np.arange(100,112),'high':np.arange(101,113),'low':np.arange(99,111),'close':np.arange(100,112)}).to_csv(p,index=False)
    cfg=Config(wma_period=5,atr_period=3,force_square_off=False)
    metrics,_=run(p,tmp_path/'results',cfg,start='2026-11-23 09:20')
    out=pd.read_csv(tmp_path/'results/bars_and_signals.csv')
    assert out.iloc[0].wma==pytest.approx((101+2*102+3*103+4*104+5*105)/15)
    assert str(metrics['execution_start']).startswith('2026-11-23 09:20')


def test_disabling_squareoff_alone_preserves_existing_entry_cutoff():
    df=bars(['2026-11-23 15:14','2026-11-23 15:15','2026-11-24 09:15'],buys=(0,))
    trades,events,_,position,pending=simulate(df,Config(force_square_off=False))
    assert len(trades)==0 and position is None and pending is None
    assert 'BUY_BLOCKED' in set(events.action)
