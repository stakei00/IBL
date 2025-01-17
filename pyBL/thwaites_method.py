#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Implementations of Thwaites' method.

This module contains the necessary classes and data for the implementation of
Thwaites' one equation integral boundary layer method. There are two concrete
implementations: :class:`ThwaitesMethodLinear` that is based on the traditional
assumption that the ODE to be solved fits a linear relationship, and
:class:`ThwaitesMethodNonlinear` that removes the linear relationship
assumption and provides slightly better results in all cases tested.
"""

from abc import abstractmethod
from typing import Tuple
import numpy as np
import numpy.typing as np_type
from scipy.interpolate import CubicSpline
from scipy.misc import derivative as fd

from pyBL.ibl_method import IBLMethod
from pyBL.ibl_method import IBLTermEvent
from pyBL.initial_condition import ManualCondition


class ThwaitesMethod(IBLMethod):
    """
    Base class for Thwaites' Method.

    This class models a laminar boundary layer using Thwaites' Method from,
    “Approximate Calculation of the Laminar Boundary Layer.” **The Aeronautical
    Journal**, Vol. 1, No. 3, 1949, pp. 245–280. It is the base class for the
    linear (:class:`ThwaitesMethodLinear`)and nonlinear
    (:class:`ThwaitesMethodNonlinear`) versions of Thwaites method.

    In addition to the :class:`IBLMethod` configuration information, the
    initial momentum thickness is needed along with the kinematic viscosity.
    Thwaites' original algorithm relied upon tabulated data for the analysis,
    and there are few different ways of modeling that data in this class.
    """

    # Attributes
    #    _model: Collection of functions for S, H, and H'
    def __init__(self, nu: float = 1.0, U_e=None, dU_edx=None, d2U_edx2=None,
                 data_fits="Spline"):
        super().__init__(nu, U_e, dU_edx, d2U_edx2)

        self.set_data_fits(data_fits)

    def set_initial_parameters(self, delta_m0: float) -> None:
        """
        Set the initial conditions for the solver.

        Parameters
        ----------
        delta_m0: float
            Momentum thickness at start location.

        Raises
        ------
        ValueError
            When negative invalid initial conditions
        """
        if delta_m0 < 0:
            raise ValueError("Initial momentum thickness must be positive")

        ic = ManualCondition(0.0, delta_m0, 0)
        self.set_initial_condition(ic)

    def set_data_fits(self, data_fits):
        """
        Set the data fit functions.

        This method sets the functions used for the data fits of the shear
        function, shape function, and the slope of the shape function.

        Parameters
        ----------
            data_fits: 2-tuple, 3-tuple, or string
                The data fits can be set via one of the following methods:
                    - 3-tuple of callable objects taking one parameter that
                      represent the shear function, the shape function, and
                      the derivative of the shape function;
                    - 2-tuple of callable objects taking one parameter that
                      represent the shear function and the shape function.
                      The derivative of the shear function is then
                      approximated using finite differences; or
                    - String for representing one of the three internal
                      implementations:

                         - "Spline" for spline fits of Thwaites original
                           data (Edland 2022)
                         - "White" for the curve fits from White (2011)
                         - "Cebeci-Bradshaw" for curve fits from
                           Cebeci-Bradshaw (1977)

        Raises
        ------
        ValueError
            When an invalid fit name or unusable 2-tuple or 3-tuple provided
        """
        # pylint: disable=too-many-branches
        # data_fits can either be string or 2-tuple of callables
        self._model = None
        if isinstance(data_fits, str):
            if data_fits == "Spline":
                self._model = _ThwaitesFunctionsSpline()
            elif data_fits == "White":
                self._model = _ThwaitesFunctionsWhite()
            elif data_fits == "Cebeci-Bradshaw":
                self._model = _ThwaitesFunctionsCebeciBradshaw()
            else:
                raise ValueError("Unknown fitting function name: ", data_fits)
        else:
            # check to make sure have two callables
            if isinstance(data_fits, tuple):
                if len(data_fits) == 3:
                    if callable(data_fits[0]) and callable(data_fits[1]) \
                            and callable(data_fits[2]):
                        self._model = _ThwaitesFunctions("Custom",
                                                         data_fits[0],
                                                         data_fits[1],
                                                         data_fits[2],
                                                         -np.inf, np.inf)
                    else:
                        raise ValueError("Need to pass callable objects for "
                                         "fit functions")
                elif len(data_fits) == 2:
                    if callable(data_fits[0]) and callable(data_fits[1]):
                        def Hp_fun(lam):
                            return fd(self._model.H, lam, 1e-5, n=1, order=3)
                        self._model = _ThwaitesFunctions("Custom",
                                                         data_fits[0],
                                                         data_fits[1],
                                                         Hp_fun,
                                                         -np.inf, np.inf)
                    else:
                        raise ValueError("Need to pass callable objects for "
                                         "fit functions")
                else:
                    raise ValueError("Need to pass two or three callable "
                                     "objects for fit functions")
            else:
                raise ValueError("Need to pass a 2-tuple for fit functions")

        self._set_kill_event(_ThwaitesSeparationEvent(self._calc_lambda,
                                                      self._model.S))

    def V_e(self, x):
        """
        Calculate the transpiration velocity.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired transpiration velocity at the specified locations.
        """
        U_e = self.U_e(x)
        dU_edx = self.dU_edx(x)
        delta_m2_on_nu = self._solution(x)[0]
        term1 = dU_edx*self.delta_d(x)
        term2 = np.sqrt(self._nu/delta_m2_on_nu)
        dsol_dx = self._ode_impl(x, delta_m2_on_nu)
        term3 = 0.5*U_e*self.H_d(x)*dsol_dx
        term4a = self._model.Hp(self._calc_lambda(x, delta_m2_on_nu))
        term4 = U_e*delta_m2_on_nu*term4a
        term5 = dU_edx*dsol_dx+self.d2U_edx2(x)*delta_m2_on_nu
        return term1 + term2*(term3+term4*term5)

    def delta_d(self, x):
        """
        Calculate the displacement thickness.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired displacement thickness at the specified locations.
        """
        return self.delta_m(x)*self.H_d(x)

    def delta_m(self, x):
        """
        Calculate the momentum thickness.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired momentum thickness at the specified locations.
        """
        return np.sqrt(self._solution(x)[0]*self._nu)

    def delta_k(self, x):
        """
        Calculate the kinetic energy thickness.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired kinetic energy thickness at the specified locations.
        """
        return np.zeros_like(x)

    def H_d(self, x):
        """
        Calculate the displacement shape factor.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired displacement shape factor at the specified locations.
        """
        lam = self._calc_lambda(x, self._solution(x)[0])
        return self._model.H(lam)

    def H_k(self, x):
        """
        Calculate the kinetic energy shape factor.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.

        Returns
        -------
        array-like same shape as `x`
            Desired kinetic energy shape factor at the specified locations.
        """
        return self.delta_k(x)/self.delta_m(x)

    def tau_w(self, x, rho):
        """
        Calculate the wall shear stress.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.
        rho: float
            Freestream density.

        Returns
        -------
        array-like same shape as `x`
            Desired wall shear stress at the specified locations.
        """
        lam = self._calc_lambda(x, self._solution(x)[0])
        return rho*self._nu*self.U_e(x)*self._model.S(lam)/self.delta_m(x)

    def D(self, x, rho):
        """
        Calculate the dissipation integral.

        Parameters
        ----------
        x: array-like
            Streamwise loations to calculate this property.
        rho: float
            Freestream density.

        Returns
        -------
        array-like same shape as `x`
            Desired dissipation integral at the specified locations.
        """
        return np.zeros_like(x)

    def _ode_setup(self) -> Tuple[np_type.NDArray, float, float]:
        """
        Set the solver specific parameters.

        Returns
        -------
        3-Tuple
            IBL initialization array
            Relative tolerance for ODE solver
            Absolute tolerance for ODE solver
        """
        return np.array([self._ic.delta_m()**2/self._nu]), 1e-8, 1e-11

    def _ode_impl(self, x, F):
        """
        Right-hand-side of the ODE representing Thwaites method.

        Parameters
        ----------
        x: array-like
            Streamwise location of current step.
        F: array-like
            Current step's square of momentum thickness divided by the
            kinematic viscosity.

        Returns
        -------
        array-like same shape as `F`
            The right-hand side of the ODE at the given state.
        """
        return self._calc_F(x, F)/(1e-3 + self.U_e(x))

    def _calc_lambda(self, x, delta_m2_on_nu):
        r"""
        Calculate the :math:`\lambda` term needed in Thwaites' method.

        Parameters
        ----------
        x : array-like
            Streamwise location of current step.
        delta_m2_on_nu : array-like
            Dependent variable in the ODE solver.

        Returns
        -------
        array-like same shape as `x`
            The :math:`\lambda` parameter that corresponds to the given state.
        """
        return delta_m2_on_nu*self.dU_edx(x)

    @abstractmethod
    def _calc_F(self, x, delta_m2_on_nu):
        """
        Calculate the :math:`F` term in the ODE.

        The F term captures the interaction between the shear function and the
        shape function and can be modeled as a linear expression (as the
        standard Thwaites' method does) or can be calculated directly using
        the data fit relations for the shear function and the shape function.

        Parameters
        ----------
        x : array-like
            Streamwise location of current step.
        delta_m2_on_nu : array-like
            Dependent variable in the ODE solver.

        Returns
        -------
        array-like same shape as `x`
            The calculated value of :math:`F`
        """


class ThwaitesMethodLinear(ThwaitesMethod):
    r"""
    Laminar boundary layer model using Thwaites Method linear approximation.

    Solves the original approximate ODE from Thwaites' method when provided
    the edge velocity profile. There are a few different ways of modeling the
    tabular data from Thwaites original work that can be set.

    This class solves the following differential equation using the linear
    approximation from Thwaites' original paper

    .. math::
        \frac{d}{dx}\left(\frac{\delta_m^2}{\nu}\right)
            =\frac{1}{U_e}\left(0.45-6\lambda\right)

    using the :class:`IBLMethod` ODE solver.
    """

    def _calc_F(self, x, delta_m2_on_nu):
        r"""
        Calculate the :math:`F` term in the ODE using the linear approximation.

        The F term captures the interaction between the shear function and the
        shape function and is modeled as the following linear expression (as
        the standard Thwaites' method does)

        .. math:: F\left(\lambda\right)=0.45-6\lambda

        Parameters
        ----------
        x : array-like
            Streamwise location of current step.
        delta_m2_on_nu : array-like
            Dependent variable in the ODE solver.

        Returns
        -------
        array-like same shape as `x`
            The calculated value of :math:`F`
        """
        lam = self._calc_lambda(x, delta_m2_on_nu)
        a = 0.45
        b = 6
        return a - b*lam


class ThwaitesMethodNonlinear(ThwaitesMethod):
    r"""
    Laminar boundary layer model using Thwaites' Method using exact ODE.

    Solves the original ODE from Thwaites' Method (1949) without the linear
    approximation when provided the edge velocity profile. There are a few
    different ways of modeling the tabular data from Thwaites original work
    that can be set.

    This class solves the following differential equation using the data fits
    for the shear function, :math:`S`, and the shape function, :math`H`, to
    capture a more accurate representation of the laminar boundary layer flow

    .. math::
        \frac{d}{dx}\left(\frac{\delta_m^2}{\nu}\right)
            =\frac{2}{U_e}\left[S-\lambda\left(H+2\right)\right]

    using the :class:`IBLMethod` ODE solver.
    """

    def _calc_F(self, x, delta_m2_on_nu):
        r"""
        Calculate the :math:`F` term in the ODE using the actual relationship.

        The F term captures the interaction between the shear function and the
        shape function and is modeled as the original ODE expression from
        Thwaites' paper as

        .. math:: F\left(\lambda\right)=2\left[S-\lambda\left(H+2\right)\right]

        Parameters
        ----------
        x : array-like
            Streamwise location of current step.
        delta_m2_on_nu : array-like
            Dependent variable in the ODE solver.

        Returns
        -------
        array-like same shape as `x`
            The calculated value of :math:`F`
        """
        lam = self._calc_lambda(x, delta_m2_on_nu)
        return self._model.F(lam)


class _ThwaitesFunctions:
    """Base class for curve fits for Thwaites data."""

    def __init__(self, name, S_fun, H_fun, Hp_fun, lambda_min, lambda_max):
        # pylint: disable=too-many-arguments
        self._range = [lambda_min, lambda_max]
        self._name = name
        self._H_fun = H_fun
        self._Hp_fun = Hp_fun
        self._S_fun = S_fun

    def range(self):
        """Return a 2-tuple for the start and end of range."""
        return self._range[0], self._range[1]

    def H(self, lam):
        """Return the H term."""
        return self._H_fun(self._check_range(lam))

    def Hp(self, lam):
        """Return the H' term."""
        return self._Hp_fun(self._check_range(lam))

    def S(self, lam):
        """Return the S term."""
        return self._S_fun(self._check_range(lam))

    def F(self, lam):
        """Return the F term."""
        return 2*(self.S(lam) - lam*(self.H(lam)+2))

    def get_name(self):
        """Return name of function set."""
        return self._name

    def _check_range(self, lam):
        lam_min, lam_max = self.range()
        lam_local = np.array(lam)

        if (lam_local < lam_min).any():
            lam_local[lam_local < lam_min] = lam_min
#            raise ValueError("Cannot pass value less than {} into this "
#                             "function: {}".format(lam_min, lam))
        elif (lam_local > lam_max).any():
            lam_local[lam_local > lam_max] = lam_max
#            raise ValueError("Cannot pass value greater than {} into this "
#                             "function: {}".format(lam_max, lam))
        return lam_local


class _ThwaitesFunctionsWhite(_ThwaitesFunctions):
    """Returns White's calculation of Thwaites functions."""

    def __init__(self):
        def S(lam):
            return pow(lam + 0.09, 0.62)

        def H(lam):
            z = 0.25 - lam
            return 2 + z*(4.14 + z*(-83.5 + z*(854 + z*(-3337 + z*4576))))

        def Hp(lam):
            z = 0.25 - lam
            return -(4.14 + z*(-2*83.5 + z*(3*854 + z*(-4*3337 + z*5*4576))))

        super().__init__("White", S, H, Hp, -0.09, np.inf)


class _ThwaitesFunctionsCebeciBradshaw(_ThwaitesFunctions):
    """Returns Cebeci and Bradshaw's calculation of Thwaites functions."""

    def __init__(self):
        def S(lam):
            return np.piecewise(lam, [lam < 0, lam >= 0],
                                [lambda lam: (0.22 + 1.402*lam
                                              + 0.018*lam/(0.107 + lam)),
                                 lambda lam: 0.22 + 1.57*lam - 1.8*lam**2])

        def H(lam):
            # NOTE: C&B's H function is not continuous at lam=0,
            #       so using second interval
            return np.piecewise(lam, [lam < 0, lam >= 0],
                                [lambda lam: 2.088 + 0.0731/(0.14 + lam),
                                 lambda lam: 2.61 - 3.75*lam + 5.24*lam**2])

        def Hp(lam):
            # NOTE: C&B's H function is not continuous at lam=0,
            #       so using second interval
            return np.piecewise(lam, [lam < 0, lam >= 0],
                                [lambda lam: -0.0731/(0.14 + lam)**2,
                                 lambda lam: -3.75 + 2*5.24*lam])

        super().__init__("Cebeci and Bradshaw", S, H, Hp, -0.1, 0.1)


class _ThwaitesFunctionsSpline(_ThwaitesFunctions):
    """Returns cubic splines of Thwaites tables based on Edland 2021."""

    def __init__(self):
        # Spline fits to Thwaites original data Edland
        S = CubicSpline(self._tab_lambda, self._tab_S)
        H = CubicSpline(self._tab_lambda, self._tab_H)
        Hp = H.derivative()

        super().__init__("Thwaites Splines", S, H, Hp,
                         np.min(self._tab_lambda), np.max(self._tab_lambda))

    # Tabular data section
    _tab_F = np.array([0.938, 0.953, 0.956, 0.962, 0.967, 0.969, 0.971, 0.970,
                       0.963, 0.952, 0.936, 0.919, 0.902, 0.886, 0.854, 0.825,
                       0.797, 0.770, 0.744, 0.691, 0.640, 0.590, 0.539, 0.490,
                       0.440, 0.342, 0.249, 0.156, 0.064,-0.028,-0.138,-0.251,
                      -0.362, -0.702, -1.000])
    _tab_S = np.array([0.000, 0.011, 0.016, 0.024, 0.030, 0.035, 0.039, 0.049,
                       0.055, 0.067, 0.076, 0.083, 0.089, 0.094, 0.104, 0.113,
                       0.122, 0.130, 0.138, 0.153, 0.168, 0.182, 0.195, 0.208,
                       0.220, 0.244, 0.268, 0.291, 0.313, 0.333, 0.359, 0.382,
                       0.404, 0.463, 0.500])
    _tab_H = np.array([3.70, 3.69, 3.66, 3.63, 3.61, 3.59, 3.58, 3.52, 3.47,
                       3.38, 3.30, 3.23, 3.17, 3.13, 3.05, 2.99, 2.94, 2.90,
                       2.87, 2.81, 2.75, 2.71, 2.67, 2.64, 2.61, 2.55, 2.49,
                       2.44, 2.39, 2.34, 2.28, 2.23, 2.18, 2.07, 2.00])
    _tab_lambda = np.array([-0.082,-0.0818,-0.0816,-0.0812,-0.0808,-0.0804,
                            -0.080,-0.079, -0.078, -0.076, -0.074, -0.072,
                            -0.070,-0.068, -0.064, -0.060, -0.056, -0.052,
                            -0.048,-0.040, -0.032, -0.024, -0.016, -0.008,
                            +0.000, 0.016,  0.032,  0.048,  0.064,  0.080,
                            +0.10,  0.12,   0.14,   0.20,   0.25])


class _ThwaitesSeparationEvent(IBLTermEvent):
    """
    Detects separation and will terminate integration when it occurs.

    This is a callable object that the ODE integrator will use to determine if
    the integration should terminate before the end location.
    """

    # pylint: disable=too-few-public-methods
    # Attributes
    # ----------
    #    _calc_lam: Callable that can calculate lambda.
    #    _S_fun: Callable that can calculate the shear function.
    def __init__(self, calc_lam, S_fun):
        super().__init__()
        self._calc_lam = calc_lam
        self._S_fun = S_fun

    def _call_impl(self, x, F):
        """
        Help determine if Thwaites method integrator should terminate.

        This will terminate once the shear function goes negative.

        Parameters
        ----------
        x: array-like
            Current x-location of the integration.
        F: array-like
            Current step square of momentum thickness divided by the
            kinematic viscosity.

        Returns
        -------
        float
            Current value of the shear function.
        """
        return self._S_fun(self._calc_lam(x, F))

    def event_info(self):
        return -1, ""
