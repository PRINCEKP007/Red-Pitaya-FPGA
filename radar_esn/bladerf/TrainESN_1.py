import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from lib import create_input_matrix,compress_signal,add_complex_awgn,generate_chirp_pulse,create_local_measurement_matrix,create_reservoir_matrix,run_reservoir,energy_mean_pooling,calculate_metrics
import random
np.random.seed(42)
random.seed(42)
Fs = 20e6

# A = np.memmap(
#     "ESN_Train.bin",
#     dtype="<c16",          
#     mode="r",
#     shape=(30720, 6000),
#     order="F"           
# )

A = np.load("X_3PRI.npy", mmap_mode="r")
A = A.reshape(30720, 20000, order="F")

print(A.shape)             
print(A.dtype)            

PW_raw = np.genfromtxt("PW.csv", delimiter=",")
BW_raw = np.genfromtxt("BW.csv",delimiter=",")

PW = PW_raw[0:2000]
BW = BW_raw[0:2000]

PW_Full = np.zeros((2000,10))
BW_Full = np.zeros((2000,10))

PW_Full[:,0] = PW
PW_Full[:,1] = PW
PW_Full[:,2] = PW
PW_Full[:,3] = PW
PW_Full[:,4] = PW
PW_Full[:,5] = PW
PW_Full[:,6] = PW
PW_Full[:,7] = PW
PW_Full[:,8] = PW
PW_Full[:,9] = PW

BW_Full[:,0] = BW
BW_Full[:,1] = BW
BW_Full[:,2] = BW
BW_Full[:,3] = BW
BW_Full[:,4] = BW
BW_Full[:,5] = BW
BW_Full[:,6] = BW
BW_Full[:,7] = BW
BW_Full[:,8] = BW
BW_Full[:,9] = BW

PW_Full = PW_Full.reshape(20000,1)
BW_Full = BW_Full.reshape(20000,1)

Ntrain = 19000
Ntest = 1000

reservoir_density = 0.02
desired_radius = 0.6
input_scale = 0.5
N_res = 1000

reservoir_density = 0.02
desired_radius = 0.9
input_scale = 0.5

BLOCK_SIZE = 10
STRIDE = 10
N = len(A)
M_local = 1
N_blocks = N // STRIDE
ridge_alpha = 1e-2


print("\n============================================================")
print("TRAINING DATASET")
print("============================================================")



Phi_local = create_local_measurement_matrix(M_local,BLOCK_SIZE)
Wres = create_reservoir_matrix(N_res,reservoir_density,desired_radius)
Win = create_input_matrix(N_res,M_local,input_scale)

X_train = []
Y_train = []

X_test = []
Y_test = []

print("\n Training signals...")

N_train = 19000
B = A[:,0:N_train]
C = A[:,Ntrain::]

PW_Train = PW_Full[0:N_train]
BW_Train = BW_Full[0:N_train]

PW_Test = PW_Full[Ntrain::]
BW_Test = BW_Full[Ntrain::]

for k in range(N_train):

    PW_ = PW_Train[k]
    BW_ = BW_Train[k]

    x_noisy = B[:,k]
    Y_compressed = compress_signal(x_noisy,Phi_local,BLOCK_SIZE,STRIDE)
    states = run_reservoir(Y_compressed,Win,Wres)

    features = energy_mean_pooling(states)

    X_train.append(features)
    Y_train.append([PW_,BW_ / 1e3])

    if (k + 1) % 100 == 0:

        print(
            f"Training signals : "
            f"{k + 1}/{N_train}"
        )

print("\n Test signals...")

N_test = 1000

for k in range(N_test):

    PW_ = PW_Test[k]
    BW_ = BW_Test[k]
    x_noisy = C[:,k]

    Y_compressed = compress_signal(x_noisy,Phi_local,BLOCK_SIZE,STRIDE)
    states = run_reservoir(Y_compressed,Win,Wres)
    features = energy_mean_pooling(states)
    X_test.append(features)
    Y_test.append([PW_,BW_ / 1e3])


X_train = np.array(X_train)
Y_train = np.array(Y_train).squeeze()

X_test = np.array(X_test)
Y_test = np.array(Y_test).squeeze()


print("\nDataset shapes:")
print("X_train:", X_train.shape)
print("Y_train:", Y_train.shape)
print("X_test :", X_test.shape)
print("Y_test :", Y_test.shape)

print("\n============================================================")
print("FEATURE NORMALIZATION")
print("============================================================")


X_mean = np.mean(X_train,axis=0)
X_std = np.std(X_train,axis=0)

X_std[X_std < 1e-12] = 1.0


X_train_norm = (X_train - X_mean) / X_std
X_test_norm = ( X_test - X_mean) / X_std

print("\n============================================================")
print("TARGET NORMALIZATION")
print("============================================================")

Y_mean = np.mean(Y_train,axis=0)
Y_std = np.std(Y_train,axis=0)
Y_std[Y_std < 1e-12] = 1.0
Y_train_norm = (Y_train - Y_mean) / Y_std
Y_test_norm = (Y_test - Y_mean) / Y_std


X_train_aug = np.column_stack([np.ones(len(X_train_norm)),X_train_norm])

X_test_aug = np.column_stack([np.ones(len(X_test_norm)),X_test_norm])

print("\n============================================================")
print("TRAINING RIDGE REGRESSION")
print("============================================================")


D = X_train_aug.shape[1]
I = np.eye(D)
I[0, 0] = 0

A = (X_train_aug.T @ X_train_aug+ridge_alpha * I)
B = (X_train_aug.T @ Y_train_norm)

W_readout = np.linalg.solve(A,B)

# ============================================================
# SAVE FINAL WEIGHTS / MODEL
# ============================================================
np.savez(
    "esn_model.npz",
    # trained readout
    W_readout=W_readout,
    # fixed random parts of the reservoir pipeline
    Win=Win,
    Wres=Wres,
    Phi_local=Phi_local,
    # normalization stats
    X_mean=X_mean,
    X_std=X_std,
    Y_mean=Y_mean,
    Y_std=Y_std,
    # hyperparameters
    N_res=N_res,
    reservoir_density=reservoir_density,
    desired_radius=desired_radius,
    input_scale=input_scale,
    BLOCK_SIZE=BLOCK_SIZE,
    STRIDE=STRIDE,
    M_local=M_local,
    ridge_alpha=ridge_alpha,
    Fs=Fs,
)
print("Saved model to esn_model.npz")


Y_pred_norm = (X_test_aug @ W_readout)
Y_pred = (Y_pred_norm * Y_std+Y_mean)

PW_true_us = Y_test[:, 0]
BW_true_MHz = Y_test[:, 1]

PW_pred_us = Y_pred[:, 0]
BW_pred_MHz = Y_pred[:, 1]

M_true = (BW_true_MHz /PW_true_us) * 1e12
#PW_pred_us_safe = np.maximum(PW_pred_us,1e-12)

M_pred = (BW_pred_MHz /PW_pred_us) * 1e12




PW_metrics = calculate_metrics(PW_true_us,PW_pred_us)



(PW_rmse,PW_mae,PW_median,PW_max,PW_r2) = PW_metrics


BW_metrics = calculate_metrics(BW_true_MHz,BW_pred_MHz)

(BW_rmse,BW_mae,BW_median,BW_max,BW_r2) = BW_metrics


plt.plot(BW_true_MHz)
plt.plot(BW_pred_MHz)
plt.show()


M_metrics = calculate_metrics(M_true,M_pred)

(M_rmse,M_mae,M_median,M_max,M_r2) = M_metrics
plt.plot(M_true)
plt.plot(M_pred)    
plt.show()


M_relative_error = (np.abs(M_pred - M_true)/np.abs(M_true)) * 100
M_mean_relative_error = np.mean(M_relative_error)
M_median_relative_error = np.median(M_relative_error)
M_within_5 = np.mean(M_relative_error <= 5) * 100

print("\n")
print("============================================================")
print("EXPERIMENT 1 RESULTS")
print("ENERGY MEAN POOLING + RIDGE REGRESSION")
print("============================================================")


print("\n---------------- PW ESTIMATION ----------------")

print(f"PW RMSE                  : "f"{PW_rmse * 1e3:.4f} ns")

print(f"PW MAE                   : "f"{PW_mae * 1e3:.4f} ns")

print(f"PW Median absolute error : "f"{PW_median * 1e3:.4f} ns")

print(f"PW Maximum absolute error: "f"{PW_max * 1e3:.4f} ns")

print(f"PW R^2                   : "f"{PW_r2:.6f}")


print("\n---------------- BW ESTIMATION ----------------")

print(f"BW RMSE                  : "f"{BW_rmse * 1e3:.4f} KHz")

print(f"BW MAE                   : "f"{BW_mae * 1e3:.4f} KHz")

print(f"BW Median absolute error : "f"{BW_median * 1e3:.4f} KHz")

print(f"BW Maximum absolute error: "f"{BW_max * 1e3:.4f} KHz")

print(f"BW R^2                   : "f"{BW_r2:.6f}")

print("\n---------------- CHIRP RATE M ----------------")

print(f"M RMSE                   : "f"{M_rmse:.6e} Hz/s")

print(f"M MAE                    : "f"{M_mae:.6e} Hz/s")

print(f"M Median absolute error  : "f"{M_median:.6e} Hz/s")

print(f"M Maximum absolute error : "f"{M_max:.6e} Hz/s")

print(f"M R^2                    : "f"{M_r2:.6f}")

print(f"Mean relative error      : "f"{M_mean_relative_error:.3f}%")

print(f"Median relative error    : "f"{M_median_relative_error:.3f}%")

print(f"Within 5% relative error : "f"{M_within_5:.2f}%")

# ============================================================
# 34. SHOW SOME EXAMPLES
# ============================================================

print("\n")
print("============================================================")
print("EXAMPLE PREDICTIONS")
print("============================================================")

print("\nSample | ""PW true | PW pred | ""BW true | BW pred | ""M true (1e12) | M pred (1e12)"
)

print("-" * 90)

for i in range(
    min(10, N_test)
):

    print(
        f"{i:6d} | "
        f"{PW_true_us[i]*1e3:7.4f} | "
        f"{PW_pred_us[i]*1e3:7.4f} | "
        f"{BW_true_MHz[i]*1e3:7.3f} | "
        f"{BW_pred_MHz[i]*1e3:7.3f} | "
        f"{M_true[i]/1e12:12.4f} | "
        f"{M_pred[i]/1e12:12.4f}"
    )

print("\n")
print("============================================================")
print("EXPERIMENT CONFIGURATION")
print("============================================================")

print(
    f"Sampling frequency       : {Fs/1e6:.1f} MHz"
)

print(
    f"Observation samples      : {N}"
)

print(
    f"Training signals         : {N_train}"
)

print(
    f"Testing signals          : {N_test}"
)

print(
    f"Reservoir neurons        : {N_res}"
)
print(
    f"Reservoir density        : "
    f"{reservoir_density}"
)
print(
    f"Reservoir spectral radius: "
    f"{desired_radius}"
)
print(
    f"Compression measurements : "
    f"{M_local} per block"
)
print(
    f"Block size               : "
    f"{BLOCK_SIZE}"
)
print(
    f"Pooling                  : ENERGY MEAN"
)
print(
    f"Readout                  : RIDGE"
)
print(
    f"Ridge alpha              : "
    f"{ridge_alpha}"
)
print("\n============================================================")
print("DONE")
print("============================================================")
