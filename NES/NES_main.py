import netket as nk
import networkx as nx
import jax
from jax import numpy as jnp
from qiskit.algorithms.optimizers import COBYLA
import time
import json
import numpy as np
import matplotlib.pyplot as plt

from NES.NES_energy import MIS_energy
from NES.NES_energy import Maxcut_energy


# run NES using netket
def run_netket(cf, data, seed):
    # build objective
    if cf.pb_type == "maxindp":
        hamiltonian, graph, hilbert = MIS_energy(cf, data)
    if cf.pb_type == "maxcut":
        hamiltonian, graph, hilbert = Maxcut_energy(cf, data)

    # build model
    if cf.model_name == "rbm":
        model = nk.models.RBM(alpha=cf.width)
    elif cf.model_name == "crbm":
        model = nk.models.RBM(alpha=cf.width, dtype=np.complex64)
    # model.init_random_parameters(seed=seed, sigma=cf.param_init)
    sampler = nk.sampler.MetropolisLocal(hilbert=hilbert)

    # build optimizer
    if cf.optimizer == "adam":
        op = nk.optimizer.Adam(learning_rate=cf.learning_rate)
    elif cf.optimizer == "adagrad":
        op = nk.optimizer.AdaGrad(learning_rate=cf.learning_rate)
    elif cf.optimizer == "momentum":
        op = nk.optimizer.Momentum(learning_rate=cf.learning_rate)
    elif cf.optimizer == "rmsprop":
        op = nk.optimizer.RmsProp(learning_rate=cf.learning_rate)
    elif cf.optimizer == "sgd":
        op = nk.optimizer.Sgd(learning_rate=cf.learning_rate)

    if cf.use_sr:
        sr = nk.optimizer.SR()
    else:
        sr = None

    # # print(cf.cvar)
    # vs = nk.vqs.MCState(sampler=sampler, model=model, n_samples=cf.batch_size)
    # gs = nk.VMC(hamiltonian=hamiltonian, optimizer=op, variational_state=vs, preconditioner=sr, alpha=cf.cvar)

    if cf.cvar < 101:
        # optimize with COBYLA
        maxiter = cf.num_of_iterations
        optimizer = COBYLA(maxiter=maxiter)
        num_param = data.shape[0]**2 + 2*data.shape[0]

        obj = Objective(cf, data)
        initial_point = 0.01*jax.random.uniform(jax.random.PRNGKey(666), shape=[num_param])
        start_time = time.time()
        optimizer.optimize(num_param, obj.evaluate, initial_point=initial_point)

        # gs.run(out='result', n_iter=cf.num_of_iterations, save_params_every=cf.num_of_iterations, show_progress=True)
        end_time = time.time()
        a = np.array(obj.get_history())
        a.reshape([len(obj.get_history()),1])
        name = 'cvar_3_' + str(cf.cvar) + '_history.npy'
        np.save(name, a)

        # plot the final node assignment if specified
        size = 0
        assignment = obj.good_sample
    else:
        cf.cvar = 100
        vs = nk.vqs.MCState(sampler=sampler, model=model, n_samples=cf.batch_size)
        gs = nk.VMC(hamiltonian=hamiltonian, optimizer=op, variational_state=vs, preconditioner=sr, alpha=cf.cvar)

        # run algorithm
        start_time = time.time()
        gs.run(out='result', n_iter=cf.num_of_iterations, save_params_every=cf.num_of_iterations, show_progress=True)
        end_time = time.time()

        # plot the final node assignment if specified
        size = 0
        assignment = gs.get_good_sample()

    G = nx.from_numpy_matrix(data)
    pos = nx.circular_layout(G)
    color = []
    for i in range(data.shape[0]):
        if assignment[i] > 0:
            if cf.pb_type == 'maxindp':
                size += 1
            color.append('red')
        else:
            color.append('blue')

    if cf.pb_type == 'maxcut':
        for i in range(data.shape[0]):
            for j in range(i + 1, data.shape[0]):
                if data[i, j] == 1 and assignment[i] + assignment[j] == 0:
                    size += 1

    if cf.print_assignment:
        # print(assignment)
        nx.draw(G, pos=pos, node_color=color)
        plt.title("Node Assignment")
        plt.show()

    # plot energy vs. iterations if specified
    if cf.energy_plot:
        file = json.load(open("result.log"))
        output = file["Output"]
        energy_data = np.zeros(len(output))
        var_data = np.zeros(len(output))
        for i in range(len(output)):
            energy_data[i] = output[i]["Energy"]["Mean"]
            var_data[i] = output[i]["Energy"]["Variance"]

        plt.errorbar(np.arange(len(output)), energy_data, yerr=np.sqrt(var_data), ecolor='tab:blue', color='r')
        plt.title("Energy per Iteration")
        plt.xlabel('number of iterations')
        plt.ylabel('mean energy')
        plt.show()

        # output result

    time_elapsed = end_time - start_time
    return size, time_elapsed, assignment


# compute the CVar objective given the samples, the count, and tne energy
def cvar_obj(samples, count, O_loc, alpha):
    num_samples = samples.shape[0]

    # find cut-off
    cum_prob = 0
    j = -1
    while cum_prob < alpha:
        j += 1
        cum_prob += count[str(samples[j, :])] / num_samples
    j = min(j, num_samples)
    # compute cvar = H_j + (1/alpha)*sum_{i<j} p_i (H_i - H_j)
    if j < num_samples:
        cvar = O_loc[j]
        for i in range(j):
            cvar += (1/alpha)*(count[str(samples[i,:])]/num_samples)*(O_loc[i]-O_loc[j])
    else:
        cvar = np.mean(O_loc)

    return cvar


def custom_init(x, dtype=jnp.float_):
    def init(key, shape, dtype=dtype):
        a = blank(x)
        return a
    return init


@jax.jit
def blank(x):
    return x


class Objective:

    def __init__(self, cf, data):
        self.cf = cf
        self.data = data
        self.history = []
        self.good_sample = None
        self.t1 = 0
        self.t2 = 0
        self.t3 = 0
        self.t5 = 0

    def evaluate(self, param):
        # print('optimization: ', time.time() - self.t5)

        s = time.time()
        # setting up variational quantum state:
        # parse the parameters
        N = self.data.shape[0]
        kernel = param[0:N*N].reshape([N,N])
        bias = param[N*N:N*N+N]
        vis_bias = param[N*N+N:N*N+2*N]
        # use RBM with specified parameters
        t1 = time.time()
        model = nk.models.RBM(alpha=self.cf.width, kernel_init=custom_init(kernel), hidden_bias_init=custom_init(bias), visible_bias_init=custom_init(vis_bias))
        t2 = time.time()
        # print('set up model:', t2-t1)
        # set up graph and sampler
        t1 = time.time()
        hamiltonian, graph, hilbert = MIS_energy(self.cf, self.data)
        t2 = time.time()
        # print('set up MIS energy:', t2-t1)
        sampler = nk.sampler.MetropolisLocal(hilbert=hilbert)
        # create variational quantum state
        t3 = time.time()
        vs = nk.vqs.MCState(sampler=sampler, model=model, n_samples=self.cf.batch_size)
        t4 = time.time()
        # print('set up vs:', t4-t3)
        # print('number of samples:', vs.n_samples)
        e = time.time()
        self.t1 = e-s
        # print('setting up vqs: ', self.t1)

        s = time.time()
        # sample from RBM
        samples = vs.sample()
        samples = samples.reshape((-1, samples.shape[-1]))
        num_samples = samples.shape[0]
        e = time.time()
        self.t2 = e-s
        # print('sampling: ', self.t2)


        s = time.time()
        # calculate CVar:
        # count the number of occurrences
        # count = {}
        # for i in range(samples.shape[0]):
        #     key = str(samples[i, :])
        #     if count.get(key) == None:
        #         count[key] = 1
        #     else:
        #         count[key] += 1

        bit_string, unique_count = np.unique(samples, axis=0, return_counts=True)
        count = {}
        for i in range(bit_string.shape[0]):
            count[str(bit_string[i,:])] = unique_count[i]

        t1 = time.time()
        # print('counting: ',t1-s)


        # calculate local energy

        H = self.cf.penalty * self.data - np.eye(self.data.shape[0])
        O_loc = np.array([np.dot(np.dot(np.transpose((x + 1) / 2), H), (x + 1) / 2).squeeze() for x in samples])

        # O_loc = np.zeros(shape=[samples.shape[0]])
        # eloc_table = {}
        # for i in range(samples.shape[0]):
        #     x = (np.array(samples[i]) + 1) / 2
        #     if eloc_table.get(str(x)) == None:
        #         O_loc[i] = np.dot(np.dot(np.transpose(x), H), x).squeeze()
        #         eloc_table[str(x)] = O_loc[i]
        #     else:
        #         O_loc[i] = eloc_table[str(x)]
        t2 = time.time()
        # print('calculate energy: ', t2-t1)

        # order energy and samples in non-decreasing order
        idx = np.argsort(O_loc)
        ordered_samples = samples[idx]
        self.good_sample = ordered_samples[0]
        ordered_O_loc = O_loc[idx]

        t3 = time.time()
        # print('sorting: ', t3 - t2)

        alpha = self.cf.cvar / 100
        E_cvar = cvar_obj(ordered_samples, count, ordered_O_loc, alpha)
        e = time.time()
        self.t3 = e-s
        # print('evaluate objective: ', e - t3)
        # print('calculate cvar: ', self.t3)
        self.history.append(E_cvar)

        self.t5 = time.time()

        return E_cvar

    def get_history(self):
        return self.history