# coding: utf-8

import copy
import torch
from torch import nn

from .pretrained import alexnet, alexnet_pre
from .BasicModule import BasicModule


class ContextAlexNet(BasicModule):
    def __init__(self, num_classes):
        super(ContextAlexNet, self).__init__()

        self.conv1_1 = alexnet.features[0:3]
        self.conv1_2 = copy.deepcopy(self.conv1_1)
        self.conv1_3 = copy.deepcopy(self.conv1_1)
        self.conv2_1 = alexnet.features[3:6]
        self.conv2_2 = copy.deepcopy(self.conv2_1)
        self.conv2_3 = copy.deepcopy(self.conv2_1)
        self.conv3_1 = alexnet.features[6:8]
        self.conv3_2 = copy.deepcopy(self.conv3_1)
        self.conv3_3 = copy.deepcopy(self.conv3_1)

        self.conv4 = alexnet.features[8:10]
        self.conv5 = alexnet.features[10:13]

        self.dropout1 = alexnet.classifier[0]
        self.fc1 = alexnet.classifier[1]
        self.relu1 = alexnet.classifier[2]
        self.dropout2 = alexnet.classifier[3]
        self.fc2 = alexnet.classifier[4]
        self.relu2 = alexnet.classifier[5]
        self.fc3 = nn.Linear(in_features=4096, out_features=num_classes, bias=True)

        # for two MSE loss
        self.dconv1 = nn.Conv2d(384 * 2, 768, kernel_size=3, stride=2, padding=1)
        self.drelu1 = nn.ReLU(inplace=True)
        self.dconv2 = nn.Conv2d(768, 768, kernel_size=3, stride=1, padding=1)
        self.drelu2 = nn.ReLU(inplace=True)
        self.dpool = nn.AvgPool2d(kernel_size=7, stride=1)
        self.dfc = nn.Linear(768, 1)

    def forward(self, x1, x2, x3):
        f1 = self.conv1_1(x1)
        f1 = self.conv2_1(f1)
        f1 = self.conv3_1(f1)

        f2 = self.conv1_2(x2)
        f2 = self.conv2_2(f2)
        f2 = self.conv3_2(f2)

        f3 = self.conv1_3(x3)
        f3 = self.conv2_3(f3)
        f3 = self.conv3_3(f3)

        # add in feature
        feature = f1 + f2 + f3

        # two MSE loss
        df1_2 = self.drelu2(self.dconv2(self.drelu1(self.dconv1(torch.cat((f1, f2), 1)))))
        df2_3 = self.drelu2(self.dconv2(self.drelu1(self.dconv1(torch.cat((f2, f3), 1)))))
        diff1 = self.dfc(self.dpool(df1_2).view(df1_2.size(0), -1))
        diff2 = self.dfc(self.dpool(df2_3).view(df2_3.size(0), -1))

        feature = self.conv4(feature)
        feature = self.conv5(feature)

        feature = feature.view(feature.size(0), -1)
        out = self.fc1(self.dropout1(feature))
        out = self.fc2(self.dropout2(self.relu1(out)))
        out = self.fc3(self.relu2(out))

        # return out
        return out, diff1, diff2

