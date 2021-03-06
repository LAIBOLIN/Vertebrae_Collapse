# coding: utf-8

import copy
import torch
from torch import nn

from .pretrained import alexnet, alexnet_pre
from .BasicModule import BasicModule


class DualAlexNet(BasicModule):
    def __init__(self, num_classes):
        super(DualAlexNet, self).__init__()

        self.conv1_1 = alexnet.features[0:3]
        self.conv2_1 = alexnet.features[3:6]
        self.conv3_1 = alexnet.features[6:8]
        self.conv4_1 = alexnet.features[8:10]
        self.conv5_1 = alexnet.features[10:13]

        self.dropout1_1 = alexnet.classifier[0]
        self.fc1_1 = alexnet.classifier[1]
        self.relu1_1 = alexnet.classifier[2]
        self.dropout2_1 = alexnet.classifier[3]
        self.fc2_1 = alexnet.classifier[4]
        self.relu2_1 = alexnet.classifier[5]
        self.fc3_1 = nn.Linear(in_features=4096, out_features=num_classes, bias=True)

        self.conv1_2 = copy.deepcopy(alexnet.features[0:3])
        self.conv2_2 = copy.deepcopy(alexnet.features[3:6])
        self.conv3_2 = copy.deepcopy(alexnet.features[6:8])
        self.conv4_2 = copy.deepcopy(alexnet.features[8:10])
        self.conv5_2 = copy.deepcopy(alexnet.features[10:13])

        self.dropout1_2 = copy.deepcopy(alexnet.classifier[0])
        self.fc1_2 = copy.deepcopy(alexnet.classifier[1])
        self.relu1_2 = copy.deepcopy(alexnet.classifier[2])
        self.dropout2_2 = copy.deepcopy(alexnet.classifier[3])
        self.fc2_2 = copy.deepcopy(alexnet.classifier[4])
        self.relu2_2 = copy.deepcopy(alexnet.classifier[5])
        self.fc3_2 = nn.Linear(in_features=4096, out_features=num_classes, bias=True)

        self.fc_4 = nn.Linear(4096 * 2, 2)

    def forward(self, x, y):
        fx = self.conv1_1(x)
        fx = self.conv2_1(fx)
        fx = self.conv3_1(fx)
        fx = self.conv4_1(fx)
        fx = self.conv5_1(fx)
        fx = fx.view(fx.size(0), -1)

        fx = self.fc1_1(self.dropout1_1(fx))
        fx = self.fc2_1(self.dropout2_1(self.relu1_1(fx)))
        out_x = self.fc3_1(self.relu2_1(fx))

        fy = self.conv1_2(x)
        fy = self.conv2_2(fy)
        fy = self.conv3_2(fy)
        fy = self.conv4_2(fy)
        fy = self.conv5_2(fy)
        fy = fy.view(fy.size(0), -1)

        fy = self.fc1_2(self.dropout1_2(fy))
        fy = self.fc2_2(self.dropout2_2(self.relu1_2(fy)))
        out_y = self.fc3_2(self.relu2_2(fy))

        f_cat = torch.cat([fx, fy], 1)
        out_cat = self.fc_4(f_cat)

        return out_x, out_y, out_cat

