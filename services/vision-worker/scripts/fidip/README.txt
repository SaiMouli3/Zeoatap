Model definitions vendored from https://github.com/ostadabbas/Infant-Pose-Estimation
(commit a76b4f418107b8d3548d9779e94702e2508fbef1, lib/models/{pose_hrnet,pose_mobile,DUC}.py).
File headers say "Copyright (c) Microsoft, Licensed under the MIT License"; the FiDIP repository
README says "This code is for non-commercial purpose only". Used at the project owner's direction;
license to be obtained before sale (see ../../MODELS.md).

Changes: pose_mobile.py imports DUC without a package prefix and inlines dsntnn.flat_softmax with
torch.softmax (identical math, no dsntnn dependency); pose_hrnet.py `is '*'` -> `== '*'`.
Only used by ../export_fidip.py (needs torch). The runtime uses the exported ONNX.
