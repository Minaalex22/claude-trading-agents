// A tiny stand-in for the parts of ATAS's indicator API that JarvisFeed uses, so CI can check JarvisFeed.cs
// compiles without ATAS installed. The real build runs on the owner's computer against their own ATAS.
using System;
using System.Collections.Generic;

namespace ATAS.Indicators
{
    public enum TradeDirection { Buy, Sell, Between }
    public class PriceVolumeInfo { public decimal Price { get; set; } }
    public class IndicatorCandle
    {
        public DateTime Time { get; set; }
        public decimal Open, High, Low, Close, Volume, Delta, Ask, Bid, MaxDelta, MinDelta;
        public PriceVolumeInfo MaxVolumePriceInfo { get; set; }
    }
    public class MarketDataArg { public decimal Price, Volume; public TradeDirection Direction; }
    public class Series { public bool IsHidden { get; set; } }
    public class InstrumentInfoStub { public string Instrument { get; set; } }
    public class ChartInfoStub { public string TimeFrame { get; set; } }
    public abstract class Indicator
    {
        protected Indicator(bool useCandles) { }
        public bool DenyToChangePanel { get; set; }
        public List<Series> DataSeries { get; } = new List<Series> { new Series() };
        public int CurrentBar { get; protected set; }
        public InstrumentInfoStub InstrumentInfo { get; set; }
        public ChartInfoStub ChartInfo { get; set; }
        public IndicatorCandle GetCandle(int bar) => new IndicatorCandle();
        protected abstract void OnCalculate(int bar, decimal value);
        protected virtual void OnNewTrade(MarketDataArg trade) { }
    }
}
