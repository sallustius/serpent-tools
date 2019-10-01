"""
Class to read and process a batch of similar detector files
"""
from collections import defaultdict
import warnings

from six import iteritems
from numpy import empty, square, sqrt, allclose, asarray
from matplotlib import pyplot

from serpentTools.messages import SerpentToolsException
from serpentTools.utils import magicPlotDocDecorator, formatPlot
from serpentTools.parsers.detector import DetectorReader
from serpentTools.detectors import Detector
from serpentTools.samplers.base import Sampler, SPREAD_PLOT_KWARGS


class DetectorSampler(Sampler):
    """Class responsible for reading multiple detector files

    The following checks are performed to ensure that all detectors are
    of similar structure and content

        1. Each parser must have the same detectors
        2. The reshaped tally data must be of the same size for all detectors

    These tests can be skipped by settings ``<sampler.skipPrecheck>`` to be
    ``False``.

    Parameters
    ----------
    files: str or iterable
        Single file or iterable (list) of files from which to read.
        Supports file globs, ``*det0.m`` expands to all files that
        end with ``det0.m``

    Attributes
    ----------
    detectors : dict
        Dictionary of key, values pairs for detector names and corresponding
        :class:`~serpentTools.samplers.SampledDetector` instances
    files: set
        Unordered set containing full paths of unique files read
    settings: dict
        Dictionary of sampler-wide settings
    parsers: set
        Unordered set of all parsers that were successful
    map: dict
        Dictionary where key, value pairs are files and their corresponding
        parsers

    """

    def __init__(self, files):
        self.detectors = {}
        super().__init__(files, DetectorReader)

    def __getitem__(self, name):
        """Retrieve a detector from :attr:`detectors`."""
        return self.detectors[name]

    def _precheck(self):
        self._checkParserDictKeys('detectors')
        self._checkSizes()

    def _checkSizes(self):
        sizes = None
        for parser in self.parsers:
            if sizes is None:
                sizes = {det: {} for det in parser.detectors}
            for detName, det in iteritems(parser.detectors):
                level = sizes[detName]
                shape = det.tallies.shape
                if shape not in level:
                    level[shape] = {parser.filePath}
                else:
                    level[shape].add(parser.filePath)
        for detName, misMatches in iteritems(sizes):
            if len(misMatches) > 1:
                self._raiseErrorMsgFromDict(misMatches, 'shape', 'detector')

    def _process(self):
        individualDetectors = defaultdict(list)
        for parser in self:
            for detName, detector in parser.iterDets():
                individualDetectors[detName].append(detector)
        for name, detList in iteritems(individualDetectors):
            self.detectors[name] = SampledDetector.fromDetectors(
                name, detList)

    def _free(self):
        for sampledDet in self.detectors.values():
            sampledDet.free()

    def iterDets(self):
        for name, detector in iteritems(self.detectors):
            yield name, detector


class SampledDetector(Detector):
    """
    Class to store aggregated detector data

    Parameters
    ----------
    name : str
        Name of the detector to be sampled
    allTallies : numpy.ndarray or iterable of arrays
        Array of tally data for each individual detector
    allErrors : numpy.ndarray or iterable of arrays
        Array of absolute tally errors for individual detectors
    indexes : collections.OrderedDict, optional
        Dictionary indicating the ordering of underlying indices
    grids : dict, optional
        Additional grid information, like spatial or energy-wise
        grid information.

    Attributes
    ----------
    name : str
        Name of this detector
    tallies : numpy.ndarray
        Average of tallies from all detectors
    errors : numpy.ndarray
        Uncertainty on :attr:`tallies` after propagating uncertainty from all
        individual detectors
    deviation : numpy.ndarray
        Deviation across all tallies
    allTallies : numpy.ndarray
        Array of tally data from sampled detectors. First dimension is the
        file index ``i``, followed by the tally array for detector ``i``.
    allErrors : numpy.ndarray
        Array of uncertainties for sampled detectors. Structure is identical
        to :attr:`allTallies`
    grids : dict or None
        Dictionary of additional grid information
    indexes : collections.OrderedDict or None
        Underlying indices used in constructing the tally data

    See Also
    --------
    :meth:`fromDetectors`

    """

    def __init__(self, name, allTallies, allErrors, indexes=None, grids=None):
        # average tally data, propagate uncertainty
        self._allTallies = allTallies
        self._allErrors = allErrors
        tallies = self.allTallies.mean(axis=0)

        # propagate absolute uncertainty
        # assume no covariance
        inner = square(allErrors).sum(axis=0)
        errors = sqrt(inner) / allTallies.shape[0]
        nz = tallies.nonzero()
        errors[nz] /= tallies[nz]

        super().__init__(name, tallies=tallies, errors=errors,
                         grids=grids, indexes=indexes)
        self.deviation = self.allTallies.std(axis=0)

    @property
    def allTallies(self):
        return self._allTallies

    @allTallies.setter
    def allTallies(self, tallies):
        if tallies is None:
            self._allTallies = None
            return

        tallies = asarray(tallies)

        if self._allTallies is None:
            self._allTallies = tallies
            return

        if tallies.shape != self._tallies.shape:
            raise ValueError("Expected shape to be {}, is {}".format(
                self._allTallies.shape, tallies.shape))

        self._allTallies = tallies

    @property
    def allErrors(self):
        return self._allErrors

    @allErrors.setter
    def allErrors(self, errors):
        if errors is None:
            self._allErrors = None
            return

        errors = asarray(errors)

        if self._allErrors is None:
            self._allErrors = errors
            return

        if errors.shape != self._errors.shape:
            raise ValueError("Expected shape to be {}, is {}".format(
                self._allErrors.shape, errors.shape))

        self._allErrors = errors

    @magicPlotDocDecorator
    def spreadPlot(self, xdim=None, fixed=None, ax=None, xlabel=None,
                   ylabel=None, logx=False, logy=False, loglog=False,
                   legend=True):
        """
        Plot the mean tally value against all sampled detector data.

        Parameters
        ----------
        xdim: str
            Bin index to place on the x-axis
        fixed: None or dict
            Dictionary controlling the reduction in data down to one dimension
        {ax}
        {xlabel}
        {ylabel}
        {logx}
        {logy}
        {loglog}
        {legend}

        Returns
        -------
        {rax}

        Raises
        ------
        AttributeError
            If ``allTallies`` is None, indicating this object has been
            instructed to free up data from all sampled files
        :class:`~serpentTools.SerpentToolsException`
            If data to be plotted, after applying ``fixed``, is not
            one dimensional

        """
        if self.allTallies is None:
            raise AttributeError(
                "allTallies is None, cannot plot all tally data")

        samplerData = self.slice(fixed, 'tallies')
        slices = self._getSlices(fixed)
        if len(samplerData.shape) != 1:
            raise SerpentToolsException(
                'Data must be constrained to 1D, not {}'.format(
                    samplerData.shape))
        xdata, autoX = self._getPlotXData(xdim, samplerData)
        xlabel = xlabel or autoX
        ax = ax or pyplot.gca()
        for data in self.allTallies:
            ax.plot(xdata, data[slices], **SPREAD_PLOT_KWARGS)

        ax.plot(xdata, samplerData, label='Mean value - N={}'.format(
            self.allTallies.shape[0]))
        formatPlot(ax, logx=logx, logy=logy, loglog=loglog, xlabel=xlabel,
                   ylabel=ylabel, legend=legend)
        return ax

    @classmethod
    def fromDetectors(cls, name, detectors):
        """
        Create a :class:`SampledDetector` from similar detectors

        Parameters
        ----------
        name : str
            Name of this detector
        detectors : iterable of :class:`serpentTools.Detector`
            Iterable that contains detectors to be averaged. These
            should be structured identically, in shape of the tally
            data and the underlying grids and indexes.

        Returns
        -------
        SampledDetector

        Raises
        ------
        TypeError
            If something other than a :class:`serpentTools.Detector` is found
        ValueError
            If tally data are not shaped consistently
        KeyError
            If some grid or index information is missing
        AttributeError
            If one detector is missing grids entirely but grids are
            present on other grids
        """
        shape = None
        indexes = None
        grids = {}
        differentGrids = set()

        for d in detectors:
            if not isinstance(d, Detector):
                raise TypeError(
                    "All items should be Detector. Found {}".format(type(d)))

            if shape is None:
                shape = d.tallies.shape
            elif shape != d.tallies.shape:
                raise ValueError(
                    "Shapes do not agree. Found {} and {}".format(
                        shape, d.tallies.shape))

            # Inspect tally structure via indexes
            if indexes is None and d.indexes is not None:
                indexes = d.indexes
            else:
                # Iterate over all indexes
                for key, refIx in iteritems(indexes):
                    thisIndex = d.indexes.get(key)
                    if thisIndex is None:
                        raise KeyError(
                            "Detector {} is missing {} index".format(d, key))

            # Inspect tally structure via grids
            if d.grids and not grids:
                grids = d.grids
            elif not d.grids and grids:
                raise AttributeError(
                    "Detector {} is missing grid structure".format(d))
            elif d.grids and grids:
                for key, refGrid in iteritems(grids):
                    thisGrid = d.grids.get(key)
                    if thisGrid is None:
                        raise KeyError(
                            "Detector {} is missing {} grid".format(d, key))
                    if not allclose(refGrid, thisGrid):
                        differentGrids.add(key)

        if differentGrids:
            warnings.warn(
                "Found some potentially different grids {}".format(
                    ", ".join(differentGrids)), RuntimeWarning)

        shape = (len(detectors), ) + shape

        allTallies = empty(shape)
        allErrors = empty(shape)

        for ix, d in enumerate(detectors):
            allTallies[ix] = d.tallies
            allErrors[ix] = d.tallies * d.errors

        return cls(name, allTallies, allErrors, indexes=indexes, grids=grids)
