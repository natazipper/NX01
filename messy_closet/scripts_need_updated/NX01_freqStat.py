#!/usr/bin/env python

"""
Created by stevertaylor
Copyright (c) 2014 Stephen R. Taylor

Code contributions by Rutger van Haasteren (piccard) and Justin Ellis (PAL/PAL2).

"""

import numpy as np
from numpy import *
import os
import math
from scipy import integrate
from scipy import optimize as sciopt
from scipy import constants
from numpy import random
from scipy import special as ss
from scipy import linalg as sl
import matplotlib
#matplotlib.use('TkAgg')
matplotlib.use('macosx')
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter, LinearLocator, NullFormatter, NullLocator, AutoMinorLocator
import matplotlib.ticker
import numexpr as ne
import optparse
import cProfile
import ephem
from ephem import *
import PALInferencePTMCMC as PAL
import libstempo as T2
import time
from time import gmtime, strftime
import NX01_AnisCoefficients as anis
import NX01_utils as utils
import NX01_psr
import NX01_bayesutils as bu

parser = optparse.OptionParser(description = 'NX01 - Precursor to the PANTHER Group ENTERPRISE project')

############################
############################

parser.add_option('--lmax', dest='LMAX', action='store', type=int, default=0,
                   help='Maximum multipole for generalised OS (default = 0, i.e. isotropic OS)')
parser.add_option('--snr-tag', dest='snr_tag', action='store', type=float, default=0.9, 
                   help='Do you want the 90%, 95% or 100% SNR dataset? [6, 11, and 41 pulsars respectively] (default=0.90)')
parser.add_option('--make-plot', dest='make_plot', action='store_true', default=False, 
                   help='Do you want to make a plot for the optimal-statistic upper-limits? (default=False)')
parser.add_option('--limit-or-detect', dest='limit_or_detect', action='store', type=str, default='limit', 
                   help='Do you want to get limits or detection probabilities (default=limit)')

(args, x) = parser.parse_args()

master_path = os.getcwd()
path = '/Users/staylor/Research/EPTAv2/UniEQ'  

if args.snr_tag == 0.9:
    dir = ['J1909-3744', 'J1713+0747', 'J1744-1134', 'J0613-0200', 'J1600-3053', 'J1012+5307']   #gives 90% of total SNR^2
    snr_tag_ext = '90pct'
elif args.snr_tag == 0.95:
    dir = ['J1909-3744', 'J1713+0747', 'J1744-1134', 'J0613-0200', 'J1600-3053', 'J1012+5307', \
           'J1640+2224', 'J2145-0750', 'J1857+0943', 'J1022+1001', 'J0030+0451'] # gives 95% of total SNR^2
    snr_tag_ext = '95pct'
elif args.snr_tag == 1.0:
    os.chdir(path)
    dir = os.walk('.').next()[1]
    dir.remove('J1939+2134')
    os.chdir(master_path)
    snr_tag_ext = '100pct'

if not os.path.exists('chains_Analysis'):
    os.makedirs('chains_Analysis')

pulsars = [s for s in dir if "J" in s]
pulsars.sort()

print pulsars
################################################################################################################################
# PASSING THROUGH TEMPO2 VIA libstempo
################################################################################################################################
par_ext = 'ML'

t2psr=[]
for ii in range(len(pulsars)):
    os.chdir(path+'/'+pulsars[ii])
    if os.path.isfile('{0}_NoAFB.par'.format(pulsars[ii])):
        t2psr.append(T2.tempopulsar(parfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_TD.{0}.par'.format(par_ext),\
                                    timfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_NoAFB.tim'))
    else:
        t2psr.append(T2.tempopulsar(parfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_TD.{0}.par'.format(par_ext),\
                                    timfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_all.tim'))
    os.chdir(path)
    t2psr[ii].fit(iters=10)
    if np.any(np.isfinite(t2psr[ii].residuals())==False)==True:
        os.chdir(path+'/'+pulsars[ii])
        if os.path.isfile('{0}_NoAFB.par'.format(pulsars[ii])):
            t2psr[ii] = T2.tempopulsar(parfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_TD.{0}.par'.format(par_ext),\
                                       timfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_NoAFB.tim')
        else:
            t2psr[ii] = T2.tempopulsar(parfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_TD.{0}.par'.format(par_ext),\
                                       timfile=path+'/'+pulsars[ii]+'/'+pulsars[ii]+'_all.tim')
        os.chdir(path)

os.chdir(master_path)

################################################################################################################################
# MAKING A PULSAR OBJECT, THEN GRABBING ALL THE VARIABLES, e.g. toas, residuals, error-bars, designmatrices etc.
################################################################################################################################

psr = [NX01_psr.PsrObj(t2psr[ii]) for ii in range(len(t2psr))]

[psr[ii].grab_all_vars() for ii in range(len(psr))]

psr_positions = [np.array([psr[ii].psr_locs[0], np.pi/2. - psr[ii].psr_locs[1]]) for ii in range(len(psr))]
positions = np.array(psr_positions).copy()

CorrCoeff = np.array(anis.CorrBasis(positions,args.LMAX))       # computing all the correlation basis-functions for the array
HnD = 2.0*np.sqrt(np.pi)*CorrCoeff[0]

skyLocs = np.array([np.sin(positions[:,1])*np.cos(positions[:,0]), np.sin(positions[:,1])*np.sin(positions[:,0]), np.cos(positions[:,1])]).T
#print skyLocs.shape

angSep = np.zeros((len(psr),len(psr)))
for ii in range(len(psr)):
    for jj in range(ii,len(psr)):
        angSep[ii,jj] = np.dot(skyLocs[ii],skyLocs[jj])
        angSep[jj,ii] = angSep[ii,jj]

Tmax = np.max([psr[p].toas.max() - psr[p].toas.min() for p in range(len(psr))])
print Tmax

################################################################################################################################
# FORM A LIST COMPOSED OF NP ARRAYS CONTAINING THE INDEX POSITIONS WHERE EACH UNIQUE 'sys' BACKEND IS APPLIED
################################################################################################################################

backends = []
[psr[ii].get_backends() for ii in range(len(psr))]
for ii in range(len(psr)):
    backends.append(psr[ii].bkends)

################################################################################################################################
# GETTING MAXIMUM-LIKELIHOOD VALUES OF SINGLE-PULSAR ANALYSIS FOR OUR STARTING POINT
################################################################################################################################

Adm_ML=[]
gam_dm_ML=[]
Ared_ML=[]
gam_red_ML=[]
EFAC_ML = [[0.0]*len(backends[jj]) for jj in range(len(backends))]
EQUAD_ML = [[0.0]*len(backends[jj]) for jj in range(len(backends))]
for ii in range(len(pulsars)):
    with open(path+'/{0}/{0}_Taylor_TimeDomain_model1.txt'.format(psr[ii].name), 'r') as f:
        Adm_ML.append(float(f.readline().split()[3]))
        gam_dm_ML.append(float(f.readline().split()[3]))
        Ared_ML.append(float(f.readline().split()[3]))
        gam_red_ML.append(float(f.readline().split()[3]))
        for jj in range(len(backends[ii])):
            EFAC_ML[ii][jj] = float(f.readline().split()[3])
        for jj in range(len(backends[ii])):
            EQUAD_ML[ii][jj] = float(f.readline().split()[3])

################################################################################################################################
# MAKE FIXED NOISE MATRICES FROM MAXIMUM-LIKELIHOOD VALUES OF SINGLE-PULSAR ANALYSIS
################################################################################################################################

GCGnoiseInv=[]
for ii in range(len(psr)):
    ####################################################################
    # For each pulsar, obtain the ML A_h value with scalar maximisation
    ####################################################################
    #func = lambda x: -utils.singlePsrLL(psr[ii], x, gam_gwb=13./3.)
    #fbounded = sciopt.minimize_scalar(func, bounds=(0.0, utils.sigma_gwRMS(psr[ii]), 1.0e-13), method='Golden')
    #Agwb_ML = fbounded.x
    
    tgrid = utils.makeTimeGrid(psr[ii], psr[ii])

    #Cgwb_ML = utils.makeRedTDcov(Agwb_ML, 13./3., tgrid)
    Cred = utils.makeRedTDcov(Ared_ML[ii], gam_red_ML[ii], tgrid)
    Cdm = utils.makeDmTDcov(psr[ii], Adm_ML[ii], gam_dm_ML[ii], tgrid)
    Cwhite = np.diag(psr[ii].toaerrs**2.0)
    ########
    #GCGnoise = np.dot(psr[ii].G.T, np.dot(Cgwb_ML+Cred+Cdm+Cwhite, psr[ii].G))
    GCGnoise = np.dot(psr[ii].G.T, np.dot(Cred+Cdm+Cwhite, psr[ii].G))
    GCGnoise = np.nan_to_num(GCGnoise)
    cho = sl.cho_factor(GCGnoise)
    GCGnoiseInv.append(sl.cho_solve(cho, np.eye(len(GCGnoise))))


gam_bkgrd = 4.33333
optimalStat = utils.optStat(psr, GCGnoiseInv, HnD, gam_gwb=gam_bkgrd)
print "\n A^2 = {0}, std = {1}, SNR = {2}\n".format(optimalStat[0],optimalStat[1],optimalStat[2])

print "\n In this data, the minimum Ah of an SMBHB background that is required for 5% FAR and 68% DR is {0}\n".\
  format(np.sqrt( optimalStat[1]*np.sqrt(2.0)*( ss.erfcinv(2.0*0.05) - ss.erfcinv(2.0*0.68) ) ))
print "\n In this data, the minimum Ah of an SMBHB background that is required for 5% FAR and 95% DR is {0}\n".\
  format(np.sqrt( optimalStat[1]*np.sqrt(2.0)*( ss.erfcinv(2.0*0.05) - ss.erfcinv(2.0*0.95) ) ))

print "\n The 90% upper-limit on Ah is {0}\n".\
  format(np.sqrt( optimalStat[0] + optimalStat[1]*np.sqrt(2.0)*( ss.erfcinv(2.0*(1.-0.90)) ) ))
print "\n The 95% upper-limit on Ah is {0}\n".\
  format(np.sqrt( optimalStat[0] + optimalStat[1]*np.sqrt(2.0)*( ss.erfcinv(2.0*(1.-0.95)) ) ))


if args.make_plot:

    if args.limit_or_detect=='detect':
        far = 0.05
        dr_list = [0.95,0.68]
        bu.OSupperLimit(psr, GCGnoiseInv, HnD, optimalStat, far, dr_list)
    else:
        ul_list = [0.95,0.90]
        bu.OSupperLimit(psr, GCGnoiseInv, HnD, optimalStat, ul_list)

    bu.OScrossPower(angSep, optimalStat[3], optimalStat[4])


if args.LMAX!=0:
    anisOptStat = utils.AnisOptStat(psr, GCGnoiseInv, CorrCoeff, args.LMAX, gam_gwb=gam_bkgrd)

    print "\n The ML coefficients of an l={0} search are {1}\n".format(args.LMAX,anisOptStat[0]/np.sqrt(4.0*np.pi))
    print "\n The error-bars from the inverse Fisher matrix are {0}\n".format(np.sqrt(np.diag(anisOptStat[1]))/np.sqrt(4.0*np.pi))

    print "\n The Fisher information is {0}\n".format(anisOptStat[2])

    print "\n The ML coefficients of an l={0} search are {1}\n".format(args.LMAX,anisOptStat[0])
    print "\n The full covariance matrix is {0}\n".format(anisOptStat[1])

    np.save('mlcoeff_lmax{0}'.format(args.LMAX),anisOptStat[0])
    np.save('invfisher_lmax{0}'.format(args.LMAX),anisOptStat[1])

    psrlocs = np.loadtxt('PsrPos_SNR_{0}.txt'.format(snr_tag_ext),usecols=[1,2])
    Asqr = anisOptStat[0][0]/np.sqrt(4.0*np.pi)
    final_clm = np.array(anisOptStat[0]) / Asqr

    bu.makeSkyMap(final_clm, lmax=args.LMAX, psrs=psrlocs)
    plt.show()

    '''
    print "Fisher matrix singular values are {0}".format(anisOptStat[2])
    plt.plot(anisOptStat[2])
    plt.yscale('log')
    plt.ylabel("Fisher matrix singular value",fontsize=15)
    plt.show()
    '''

    plt.plot(anisOptStat[0]/np.sqrt(np.diag(anisOptStat[1])))
    plt.xlabel("lm mode",fontsize=15)
    plt.ylabel("ML value / error",fontsize=15)
    plt.show()
