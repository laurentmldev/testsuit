
import sys,os,pytest,math
import pandas as pd

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."))

from datatools.datatoolbox import overlap


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
    