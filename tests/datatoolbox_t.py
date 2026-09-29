
import pytest,math
import pandas as pd


from testsuit.datatools.datatoolbox import overlap


def test_overlap():
    """Ensure overlap function works as expected: output results shall have same index (based on main data reduced to smallest common time window).
   Secondary data values shall be interpolated over index of main data."""
    dfMain = pd.DataFrame({"main":[1,2,3,float("nan"),4,5]},index=[10,20,30,32,40,50])    
    dfSecondary = pd.DataFrame({"secondary":[0,0.44,0.567,1.324,2.22,3.1415]},index=[8,9,11,20,22,47])
    
    print("\n")
    print(dfMain)
    print(dfSecondary)
    
    dfMainReduced,dfSecReduced = overlap(dfMain,dfSecondary)
    
    print("\n")
    print(dfMainReduced)
    print(dfSecReduced)
    
    assert((dfMainReduced.index == dfSecReduced.index).all())
    
    assert(len(dfMainReduced)==5)
    assert(dfMainReduced.index[-1]==40)
        
    assert(math.isnan(dfMainReduced.loc[32]["main"]))
    assert(dfSecReduced.loc[40]["secondary"]==2.883480)
    

@pytest.mark.parametrize(
    "sample,expected",
    [
        ("12", None),
        ("1.5", None),
        ("2024-01-02T03:04:05Z", "2024-01-02T03:04:05+00:00"),
        ("2024-01-02T03:04:05.123Z", "2024-01-02T03:04:05.123000+00:00"),
        ("2024-01-02 03:04:05", "2024-01-02T03:04:05+00:00"),
        ("2024/01/02 03:04:05.25", "2024-01-02T03:04:05.250000+00:00"),
        ("2024-01-02Z03:04:05.1", "2024-01-02T03:04:05.100000+00:00"),
        ("17/02/2026 14:17:25.3", "2026-02-17T14:17:25.300000+00:00"),
        # National Instruments: 7 fractional digits, truncated to microseconds
        ("02/17/2026 14:17:25.3936538", "2026-02-17T14:17:25.393653+00:00"),
    ],
)
def test_getDateParser(sample, expected):
    from testsuit.datatools.datatoolbox import getDateParser
    parser = getDateParser(sample)
    if expected is None:
        assert parser is None
    else:
        assert parser(sample).isoformat() == expected


def test_getDateParser_unknown_format():
    from testsuit.datatools.datatoolbox import getDateParser
    with pytest.raises(Exception, match="Unable to parse date format"):
        getDateParser("garbage")
