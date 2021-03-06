# coding: utf-8

import os
import fire
import torch
import numpy as np

from tqdm import tqdm
from pprint import pprint
from torch.utils.data import DataLoader
from torch.nn import functional
from torchnet import meter
from sklearn.metrics import roc_curve, roc_auc_score

from config import config
from dataset import VB_Dataset, ContextVB_Dataset
from models import ContextNet
from models import FocalLoss, LabelSmoothing
from utils import Visualizer, write_csv, write_json, draw_ROC


def iter_train(**kwargs):
    config.parse(kwargs)

    # ============================================ Visualization =============================================
    # vis = Visualizer(port=2333, env=config.env)
    # vis.log('Use config:')
    # for k, v in config.__class__.__dict__.items():
    #     if not k.startswith('__'):
    #         vis.log(f"{k}: {getattr(config, k)}")

    # ============================================= Prepare Data =============================================
    train_data_1 = VB_Dataset(config.train_paths, phase='train', num_classes=config.num_classes,
                              useRGB=config.useRGB, usetrans=config.usetrans, padding=config.padding,
                              balance=config.data_balance)
    train_data_2 = VB_Dataset(config.train_paths, phase='train', num_classes=config.num_classes,
                              useRGB=config.useRGB, usetrans=config.usetrans, padding=config.padding,
                              balance=config.data_balance)
    train_data_3 = VB_Dataset(config.train_paths, phase='train', num_classes=config.num_classes,
                              useRGB=config.useRGB, usetrans=config.usetrans, padding=config.padding,
                              balance=config.data_balance)
    train_data = ContextVB_Dataset(config.train_paths, phase='test_train', num_classes=config.num_classes,
                                   useRGB=config.useRGB, usetrans=config.usetrans, padding=config.padding,
                                   balance=config.data_balance)
    val_data = ContextVB_Dataset(config.test_paths, phase='val', num_classes=config.num_classes,
                                 useRGB=config.useRGB, usetrans=config.usetrans, padding=config.padding,
                                 balance=config.data_balance)
    train_dist, val_dist = train_data_1.dist(), val_data.dist()
    train_data_scale, val_data_scale = train_data.scale, val_data.scale
    print('Training Images:', train_data_1.__len__(), 'Validation Images:', val_data.__len__())
    print('Train Data Distribution:', train_dist, 'Val Data Distribution:', val_dist)

    train_dataloader_1 = DataLoader(train_data_1, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers)
    train_dataloader_2 = DataLoader(train_data_2, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers)
    train_dataloader_3 = DataLoader(train_data_3, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers)

    train_dataloader = DataLoader(train_data, batch_size=config.batch_size, shuffle=True, num_workers=config.num_workers)
    val_dataloader = DataLoader(val_data, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers)

    # ============================================= Prepare Model ============================================
    model = ContextNet(num_classes=config.num_classes)
    print(model)

    if config.load_model_path:
        model.load(config.load_model_path)
    if config.use_gpu:
        model.cuda()
    if config.parallel:
        model = torch.nn.DataParallel(model, device_ids=list(range(config.num_of_gpu)))

    # =========================================== Criterion and Optimizer =====================================
    criterion = torch.nn.CrossEntropyLoss()
    # criterion = LabelSmoothing(size=config.num_classes, smoothing=0.2)
    # criterion = FocalLoss(gamma=4, alpha=None)
    MSELoss = torch.nn.MSELoss()
    sycriterion = torch.nn.CrossEntropyLoss()

    lr = config.lr
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=config.weight_decay)

    # ================================================== Metrics ===============================================
    log_softmax = functional.log_softmax
    loss_meter = meter.AverageValueMeter()
    mse_meter1_2 = meter.AverageValueMeter()
    mse_meter2_3 = meter.AverageValueMeter()
    syloss_meter = meter.AverageValueMeter()
    total_loss_meter = meter.AverageValueMeter()

    # ====================================== Saving and Recording Configuration =================================
    previous_AUC = 0
    previous_mAP = 0
    save_iter = 1  # 用于记录验证集上效果最好模型对应的iteration
    if config.parallel:
        save_model_dir = config.save_model_dir if config.save_model_dir else model.module.model_name
        save_model_name = config.save_model_name if config.save_model_name else model.module.model_name + '_best_model.pth'
    else:
        save_model_dir = config.save_model_dir if config.save_model_dir else model.model_name
        save_model_name = config.save_model_name if config.save_model_name else model.model_name + '_best_model.pth'
    if config.num_classes == 2:  # 2分类
        process_record = {'loss': [],  # 用于记录实验过程中的曲线，便于画曲线图
                          'train_avg': [], 'train_sp': [], 'train_se': [],
                          'val_avg': [], 'val_sp': [], 'val_se': [],
                          'train_AUC': [], 'val_AUC': []}
    elif config.num_classes == 3:  # 3分类
        process_record = {'loss': [],  # 用于记录实验过程中的曲线，便于画曲线图
                          'train_sp0': [], 'train_se0': [], 'train_sp1': [], 'train_se1': [], 'train_sp2': [], 'train_se2': [],
                          'val_sp0': [], 'val_se0': [], 'val_sp1': [], 'val_se1': [], 'val_sp2': [], 'val_se2': [],
                          'train_mAP': [], 'val_mAP': []}
    else:
        raise ValueError

    # ================================================== Training ===============================================
    iteration = 0
    # ****************************************** train ****************************************
    train_iter_1 = iter(train_dataloader_1)
    train_iter_2 = iter(train_dataloader_2)
    train_iter_3 = iter(train_dataloader_3)
    model.train()
    while iteration < config.max_iter:
        try:
            image1, label1, image_path1 = next(train_iter_1)
            image2, label2, image_path2 = next(train_iter_2)
            image3, label3, image_path3 = next(train_iter_3)
        except:
            train_iter_1 = iter(train_dataloader_1)
            train_iter_2 = iter(train_dataloader_2)
            train_iter_3 = iter(train_dataloader_3)
            image1, label1, image_path1 = next(train_iter_1)
            image2, label2, image_path2 = next(train_iter_2)
            image3, label3, image_path3 = next(train_iter_3)

        iteration += 1

        # ------------------------------------ prepare input ------------------------------------
        if config.use_gpu:
            image1 = image1.cuda()
            image2 = image2.cuda()
            image3 = image3.cuda()
            label1 = label1.cuda()
            label2 = label2.cuda()
            label3 = label3.cuda()

        # ---------------------------------- go through the model --------------------------------
        score, diff1, diff2 = model(image1, image2, image3)

        # ----------------------------------- backpropagate -------------------------------------
        # 两支之间的feature加入L2 norm
        optimizer.zero_grad()
        loss = criterion(score, label2)
        mse1_2 = MSELoss(diff1, torch.abs(label2 - label1).float())
        mse2_3 = MSELoss(diff2, torch.abs(label2 - label3).float())
        total_loss = loss + 0.1 * (mse1_2 + mse2_3)
        total_loss.backward()
        optimizer.step()

        # ------------------------------------ record loss ------------------------------------
        loss_meter.add(loss.item())
        mse_meter1_2.add(mse1_2.item())
        mse_meter2_3.add(mse2_3.item())
        total_loss_meter.add(total_loss.item())

        if iteration % config.print_freq == 0:
            tqdm.write(f"iter: [{iteration}/{config.max_iter}] {config.save_model_name[:-4]} ==================================")

            # *************************************** validate ***************************************
            if config.num_classes == 2:  # 2分类
                model.eval()
                train_cm, train_AUC, train_sp, train_se, train_T, train_accuracy = val_2class(model, train_dataloader, train_dist)
                val_cm, val_AUC, val_sp, val_se, val_T, val_accuracy = val_2class(model, val_dataloader, val_dist)
                model.train()

                # ------------------------------------ save model ------------------------------------
                # if np.average(val_se) > previous_avgse:  # 当测试集上的平均sensitivity升高时保存模型
                if val_AUC > previous_AUC:  # 当测试集上的AUC升高时保存模型
                    if config.parallel:
                        if not os.path.exists(os.path.join('checkpoints', save_model_dir, save_model_name[:-4])):
                            os.makedirs(os.path.join('checkpoints', save_model_dir, save_model_name[:-4]))
                        model.module.save(os.path.join('checkpoints', save_model_dir, save_model_name[:-4], save_model_name))
                    else:
                        if not os.path.exists(os.path.join('checkpoints', save_model_dir, save_model_name[:-4])):
                            os.makedirs(os.path.join('checkpoints', save_model_dir, save_model_name[:-4]))
                        model.save(os.path.join('checkpoints', save_model_dir, save_model_name[:-4], save_model_name))
                    # previous_avgse = np.average(val_se)
                    previous_AUC = val_AUC
                    save_iter = iteration

                # ---------------------------------- recond and print ---------------------------------
                process_record['loss'].append(loss_meter.value()[0])
                process_record['train_avg'].append((train_sp + train_se) / 2)
                process_record['train_sp'].append(train_sp)
                process_record['train_se'].append(train_se)
                process_record['train_AUC'].append(train_AUC)
                process_record['val_avg'].append((val_sp + val_se) / 2)
                process_record['val_sp'].append(val_sp)
                process_record['val_se'].append(val_se)
                process_record['val_AUC'].append(val_AUC)

                # vis.plot_many({'loss': loss_meter.value()[0],
                #                'train_avg': (train_sp + train_se) / 2, 'train_sp': train_sp, 'train_se': train_se,
                #                'val_avg': (val_sp + val_se) / 2, 'val_sp': val_sp, 'val_se': val_se,
                #                'train_AUC': train_AUC, 'val_AUC': val_AUC})
                # vis.log(f"iter: [{iteration}/{config.max_iter}] =========================================")
                # vis.log(f"lr: {optimizer.param_groups[0]['lr']}, loss: {round(loss_meter.value()[0], 5)}")
                # vis.log(f"train_avg: {round((train_sp + train_se) / 2, 4)}, train_sp: {round(train_sp, 4)}, train_se: {round(train_se, 4)}")
                # vis.log(f"val_avg: {round((val_sp + val_se) / 2, 4)}, val_sp: {round(val_sp, 4)}, val_se: {round(val_se, 4)}")
                # vis.log(f'train_AUC: {train_AUC}')
                # vis.log(f'val_AUC: {val_AUC}')
                # vis.log(f'train_cm: {train_cm}')
                # vis.log(f'val_cm: {val_cm}')
                print("lr:", optimizer.param_groups[0]['lr'], "loss:", round(loss_meter.value()[0], 5))
                print('train_avg:', round((train_sp + train_se) / 2, 4), 'train_sp:', round(train_sp, 4), 'train_se:', round(train_se, 4))
                print('val_avg:', round((val_sp + val_se) / 2, 4), 'val_sp:', round(val_sp, 4), 'val_se:', round(val_se, 4))
                print('train_AUC:', train_AUC, 'val_AUC:', val_AUC)
                print('train_cm:')
                print(train_cm)
                print('val_cm:')
                print(val_cm)

            elif config.num_classes == 3:  # 3分类
                model.eval()
                train_cm, train_mAP, train_sp, train_se, train_accuracy = val_3class(model, train_dataloader, train_data_scale)
                val_cm, val_mAP, val_sp, val_se, val_accuracy = val_3class(model, val_dataloader, val_data_scale)
                model.train()

                # ------------------------------------ save model ------------------------------------
                if val_mAP > previous_mAP:  # 当测试集上的AUC升高时保存模型
                    if config.parallel:
                        if not os.path.exists(os.path.join('checkpoints', save_model_dir, save_model_name[:-4])):
                            os.makedirs(os.path.join('checkpoints', save_model_dir, save_model_name[:-4]))
                        model.module.save(os.path.join('checkpoints', save_model_dir, save_model_name[:-4], save_model_name))
                    else:
                        if not os.path.exists(os.path.join('checkpoints', save_model_dir, save_model_name[:-4])):
                            os.makedirs(os.path.join('checkpoints', save_model_dir, save_model_name[:-4]))
                        model.save(os.path.join('checkpoints', save_model_dir, save_model_name[:-4], save_model_name))
                    previous_mAP = val_mAP
                    save_iter = iteration

                # ---------------------------------- recond and print ---------------------------------
                process_record['loss'].append(loss_meter.value()[0])
                process_record['train_sp0'].append(train_sp[0])
                process_record['train_se0'].append(train_se[0])
                process_record['train_sp1'].append(train_sp[1])
                process_record['train_se1'].append(train_se[1])
                process_record['train_sp2'].append(train_sp[2])
                process_record['train_se2'].append(train_se[2])
                process_record['train_mAP'].append(float(train_mAP))
                process_record['val_sp0'].append(val_sp[0])
                process_record['val_se0'].append(val_se[0])
                process_record['val_sp1'].append(val_sp[1])
                process_record['val_se1'].append(val_se[1])
                process_record['val_sp2'].append(val_sp[2])
                process_record['val_se2'].append(val_se[2])
                process_record['val_mAP'].append(float(val_mAP))

                # vis.plot_many({'mse': mse_meter.value()[0], 'total_loss': total_loss_meter.value()[0]})
                # vis.plot_many({'syloss': syloss_meter.value()[0], 'total_loss': total_loss_meter.value()[0]})
                # vis.plot_many({'mse1': mse_meter1_2.value()[0], 'mse2': mse_meter2_3.value()[0],
                #                'total_loss': total_loss_meter.value()[0]})
                # vis.plot_many({'loss': loss_meter.value()[0],
                #                'train_sp0': train_se[0], 'train_sp1': train_se[1], 'train_sp2': train_se[2],
                #                'train_se0': train_se[0], 'train_se1': train_se[1], 'train_se2': train_se[2],
                #                'val_sp0': val_se[0], 'val_sp1': val_se[1], 'val_sp2': val_se[2],
                #                'val_se0': val_se[0], 'val_se1': val_se[1], 'val_se2': val_se[2],
                #                'train_mAP': train_mAP, 'val_mAP': val_mAP})
                # vis.log(f"iter: [{iteration}/{config.max_iter}] =========================================")
                # vis.log(f"lr: {optimizer.param_groups[0]['lr']}, loss: {round(loss_meter.value()[0], 5)}")
                # vis.log(f"train_sp0: {round(train_sp[0], 4)}, train_sp1: {round(train_sp[1], 4)}, train_sp2: {round(train_sp[2], 4)}")
                # vis.log(f"train_se0: {round(train_se[0], 4)}, train_se1: {round(train_se[1], 4)}, train_se2: {round(train_se[2], 4)}")
                # vis.log(f"val_sp0: {round(val_sp[0], 4)}, val_sp1: {round(val_sp[1], 4)}, val_sp2: {round(val_sp[2], 4)}")
                # vis.log(f"val_se0: {round(val_se[0], 4)}, val_se1: {round(val_se[1], 4)}, val_se2: {round(val_se[2], 4)}")
                # vis.log(f"train_mAP: {train_mAP}, val_mAP: {val_mAP}")
                # vis.log(f'train_cm: {train_cm}')
                # vis.log(f'val_cm: {val_cm}')
                print("lr:", optimizer.param_groups[0]['lr'], "loss:", round(loss_meter.value()[0], 5), "mse:", round(mse_meter1_2.value()[0] + mse_meter2_3.value()[0], 5))
                print('train_sp0:', round(train_sp[0], 4), 'train_sp1:', round(train_sp[1], 4), 'train_sp2:', round(train_sp[2], 4))
                print('train_se0:', round(train_se[0], 4), 'train_se1:', round(train_se[1], 4), 'train_se2:', round(train_se[2], 4))
                print('val_sp0:', round(val_sp[0], 4), 'val_sp1:', round(val_sp[1], 4), 'val_sp2:', round(val_sp[2], 4))
                print('val_se0:', round(val_se[0], 4), 'val_se1:', round(val_se[1], 4), 'val_se2:', round(val_se[2], 4))
                print('train_mAP:', train_mAP, 'val_mAP:', val_mAP, 'mSP:', round(sum(val_sp)/3, 5), 'mSE:', round(sum(val_se)/3, 5))
                print('train_cm:')
                print(train_cm)
                print('val_cm:')
                print(val_cm)
                print('Best mAP:', previous_mAP)

            loss_meter.reset()

        # ------------------------------------ save record ------------------------------------
        if os.path.exists(os.path.join('checkpoints', save_model_dir, save_model_name.split('.')[0])):
            write_json(file=os.path.join('checkpoints', save_model_dir, save_model_name.split('.')[0], 'process_record.json'), content=process_record)

    # vis.log(f"Best Iter: {save_iter}")
    print("Best Iter:", save_iter)


def val_2class(model, dataloader, dist):
    # ============================ Prepare Metrics ==========================
    # val_cm = meter.ConfusionMeter(config.num_classes)
    # val_AUC = meter.AUCMeter()
    y_true, y_scores = [], []

    softmax = functional.softmax

    # ================================ Validate ==============================
    for i, (image, label, image_path) in tqdm(enumerate(dataloader)):

        # ******************* prepare input and go through the model *******************
        if config.use_gpu:
            image = image.cuda()
            label = label.cuda()
        image.requires_grad = False
        label.requires_grad = False

        score, _, _, _ = model(image, image)

        # *********************** confusion matrix and AUC ***********************
        # val_cm.add(softmax(score, dim=1).data, label.data)
        positive_score = np.array([item[1] for item in softmax(score, dim=1).data.cpu().numpy().tolist()])
        # val_AUC.add(positive_score, label.data)
        y_true.extend(label.data.cpu().numpy().tolist())  # 用于sklearn计算AUC和ROC
        y_scores.extend(positive_score.tolist())

    # ************************** TPR, FPR, AUC ******************************
    SKL_FPR, SKL_TPR, SKL_Thresholds = roc_curve(y_true, y_scores)
    SKL_AUC = roc_auc_score(np.array(y_true), np.array(y_scores), average='weighted')

    # ******************** Best SE, SP, Thresh, Matrix ***********************
    best_index = np.argmax(SKL_TPR - SKL_FPR, axis=0)
    best_SE, best_SP, best_T = SKL_TPR[best_index], 1 - SKL_FPR[best_index], SKL_Thresholds[best_index]
    best_confusion_matrix = [[int(round(dist['0'] * best_SP)), int(round(dist['0'] * (1 - best_SP)))],
                             [int(round(dist['1'] * (1 - best_SE))), int(round(dist['1'] * best_SE))]]

    # *********************** accuracy and sensitivity ***********************
    val_accuracy = 100. * sum([best_confusion_matrix[c][c] for c in range(config.num_classes)]) / np.sum(best_confusion_matrix)
    # val_se = [100. * best_confusion_matrix[i][i] / np.sum(best_confusion_matrix[i]) for i in range(config.num_classes)]

    return best_confusion_matrix, SKL_AUC, best_SP, best_SE, best_T, val_accuracy


def val_3class(model, dataloader, data_scale):
    # ============================ Prepare Metrics ==========================
    val_cm = meter.ConfusionMeter(config.num_classes)
    val_mAP = meter.mAPMeter()

    softmax = functional.softmax

    # ================================ Validate ==============================
    for i, (image, label, image_path) in tqdm(enumerate(dataloader)):

        # ******************* prepare input and go through the model *******************
        if config.use_gpu:
            last_image, cur_image, next_image = image[0].cuda(), image[1].cuda(), image[2].cuda()
            last_label, cur_label, next_label = label[0].cuda(), label[1].cuda(), label[2].cuda()
        else:
            last_image, cur_image, next_image = image[0], image[1], image[2]
            last_label, cur_label, next_label = label[0], label[1], label[2]

        last_image.requires_grad = False
        cur_image.requires_grad = False
        next_image.requires_grad = False
        last_label.requires_grad = False
        cur_label.requires_grad = False
        next_label.requires_grad = False

        score, _, _ = model(last_image, cur_image, next_image)

        # *********************** confusion matrix and mAP ***********************
        one_hot = torch.zeros(cur_label.size(0), 3).scatter_(1, cur_label.data.cpu().unsqueeze(1), 1)

        val_cm.add(softmax(score, dim=1).data, cur_label.data)
        val_mAP.add(softmax(score, dim=1).data, one_hot)

    # *********************** accuracy and sensitivity ***********************
    val_cm = val_cm.value()
    val_accuracy = 100. * sum([val_cm[c][c] for c in range(config.num_classes)]) / val_cm.sum()
    val_sp = [100. * (val_cm.sum() - val_cm.sum(0)[i] - val_cm.sum(1)[i] + val_cm[i][i]) / (val_cm.sum() - val_cm.sum(1)[i])
              for i in range(config.num_classes)]
    val_se = [100. * val_cm[i][i] / val_cm.sum(1)[i] for i in range(config.num_classes)]
    val_cm = val_cm / np.expand_dims(np.array(data_scale), axis=1)  # 计算指标时按照balance后的matrix来算，展示的时候还原

    return val_cm.astype(dtype=np.int32), val_mAP.value().numpy(), val_sp, val_se, val_accuracy


def test_2class(**kwargs):
    config.parse(kwargs)

    # ============================================= Prepare Data =============================================
    test_data = ContextVB_Dataset(config.test_paths, phase='test', num_classes=config.num_classes, useRGB=config.useRGB,
                                  usetrans=config.usetrans, padding=config.padding, balance=config.data_balance)
    test_dataloader = DataLoader(test_data, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers)
    test_dist = test_data.dist()

    print('Test Image:', test_data.__len__())

    # ============================================= Prepare Model ============================================
    model = ContextNet(num_classes=config.num_classes)
    print(model)

    if config.load_model_path:
        model.load(config.load_model_path)
        print('Model has been loaded!')
    else:
        print("Don't load model")
    if config.use_gpu:
        model.cuda()
    if config.parallel:
        model = torch.nn.DataParallel(model, device_ids=list(range(config.num_of_gpu)))
    model.eval()

    # =========================================== Prepare Metrics =====================================
    test_cm = meter.ConfusionMeter(config.num_classes)
    test_AUC = meter.AUCMeter()
    softmax = functional.softmax
    results = []
    y_true, y_scores = [], []

    # =========================================== Test ============================================
    for i, (image, label, image_path) in tqdm(enumerate(test_dataloader)):
        # ******************* prepare input and go through the model *******************
        if config.use_gpu:
            last_image, cur_image, next_image = image[0].cuda(), image[1].cuda(), image[2].cuda()
            last_label, cur_label, next_label = label[0].cuda(), label[1].cuda(), label[2].cuda()
        else:
            last_image, cur_image, next_image = image[0], image[1], image[2]
            last_label, cur_label, next_label = label[0], label[1], label[2]

        last_image.requires_grad = False
        cur_image.requires_grad = False
        next_image.requires_grad = False
        last_label.requires_grad = False
        cur_label.requires_grad = False
        next_label.requires_grad = False

        # score = model(last_image, cur_image, next_image)
        score, diff1, diff2 = model(last_image, cur_image, next_image)

        # *************************** confusion matrix and AUC *************************
        test_cm.add(softmax(score, dim=1).data, cur_label.data)
        positive_score = np.array([item[1] for item in softmax(score, dim=1).data.cpu().numpy().tolist()])
        test_AUC.add(positive_score, cur_label.data)  # torchnet计算AUC和ROC

        y_true.extend(cur_label.data.cpu().numpy().tolist())  # 用于sklearn计算AUC和ROC
        y_scores.extend(positive_score.tolist())

        # ******************************** record prediction results ******************************
        for l, p, ip in zip(cur_label.detach(), softmax(score, dim=1).detach(), image_path):
            if p[1] < 0.5:
                results.append((ip, int(l), 0, round(float(p[0]), 4), round(float(p[1]), 4)))
            else:
                results.append((ip, int(l), 1, round(float(p[0]), 4), round(float(p[1]), 4)))

    # ************************** TPR, FPR, AUC ******************************
    SKL_FPR, SKL_TPR, SKL_Thresholds = roc_curve(y_true, y_scores)
    SKL_AUC = roc_auc_score(np.array(y_true), np.array(y_scores), average='weighted')

    TNet_AUC, TNet_TPR, TNet_FPR = test_AUC.value()

    # ******************** Best SE, SP, Thresh, Matrix ***********************
    best_index = np.argmax(SKL_TPR - SKL_FPR, axis=0)
    best_SE, best_SP, best_T = SKL_TPR[best_index], 1 - SKL_FPR[best_index], SKL_Thresholds[best_index]
    best_confusion_matrix = [[int(round(test_dist['0'] * best_SP)), int(round(test_dist['0'] * (1 - best_SP)))],
                             [int(round(test_dist['1'] * (1 - best_SE))), int(round(test_dist['1'] * best_SE))]]

    # *********************** accuracy and sensitivity ***********************
    test_accuracy = 100. * sum([test_cm.value()[c][c] for c in range(config.num_classes)]) / np.sum(test_cm.value())
    test_se = [100. * test_cm.value()[i][i] / np.sum(test_cm.value()[i]) for i in range(config.num_classes)]

    # ================================ Save and Print Prediction Results ===========================
    if config.result_file:
        write_csv(os.path.join('results', config.result_file), tag=['path', 'label', 'predict', 'p1', 'p2'], content=results)

    draw_ROC(tpr=SKL_TPR, fpr=SKL_FPR, best_index=best_index, tangent=True, save_path=os.path.join('results', config.load_model_path.split('/')[-1][:-4] + "_ROC.png"))

    print('test_acc:', test_accuracy)
    print('test_avgse:', round(np.average(test_se), 4), 'train_se0:', round(test_se[0], 4), 'train_se1:', round(test_se[1], 4))
    print('SKL_AUC:', SKL_AUC, 'TNet_AUC:', TNet_AUC)
    print('Best_SE:', best_SE, 'Best_SP:', best_SP, 'Best_Threshold:', best_T)
    print('test_cm:')
    print(best_confusion_matrix)


def test_3class(**kwargs):
    config.parse(kwargs)

    # ============================================= Prepare Data =============================================
    test_data = ContextVB_Dataset(config.test_paths, phase='test', num_classes=config.num_classes, useRGB=config.useRGB,
                                  usetrans=config.usetrans, padding=config.padding, balance=config.data_balance)
    test_dataloader = DataLoader(test_data, batch_size=config.batch_size, shuffle=False, num_workers=config.num_workers)

    test_dist, test_scale = test_data.dist(), test_data.scale

    print('Test Image:', test_data.__len__())
    print('Test Data Distribution:', test_dist)

    # ============================================= Prepare Model ============================================
    model = ContextNet(num_classes=config.num_classes)
    print(model)

    if config.load_model_path:
        model.load(config.load_model_path)
        print('Model has been loaded!')
    else:
        print("Don't load model")
    if config.use_gpu:
        model.cuda()
    if config.parallel:
        model = torch.nn.DataParallel(model, device_ids=[x for x in range(config.num_of_gpu)])
    model.eval()

    # ============================ Prepare Metrics ==========================
    test_cm = meter.ConfusionMeter(config.num_classes)
    test_mAP = meter.mAPMeter()
    results = []

    softmax = functional.softmax

    # ================================== Test ===============================
    for i, (image, label, image_path) in tqdm(enumerate(test_dataloader)):

        # ******************* prepare input and go through the model *******************
        if config.use_gpu:
            last_image, cur_image, next_image = image[0].cuda(), image[1].cuda(), image[2].cuda()
            last_label, cur_label, next_label = label[0].cuda(), label[1].cuda(), label[2].cuda()
        else:
            last_image, cur_image, next_image = image[0], image[1], image[2]
            last_label, cur_label, next_label = label[0], label[1], label[2]

        last_image.requires_grad = False
        cur_image.requires_grad = False
        next_image.requires_grad = False
        last_label.requires_grad = False
        cur_label.requires_grad = False
        next_label.requires_grad = False

        score, diff1, diff2 = model(last_image, cur_image, next_image)

        # *********************** confusion matrix and mAP ***********************
        one_hot = torch.zeros(cur_label.size(0), 3).scatter_(1, cur_label.data.cpu().unsqueeze(1), 1)

        test_cm.add(softmax(score, dim=1).data, cur_label.data)
        test_mAP.add(softmax(score, dim=1).data, one_hot)

        # ******************************** record prediction results ******************************
        for l, p, ip in zip(cur_label.detach(), softmax(score, dim=1).detach(), image_path):
            if p[0] > p[1] and p[0] > p[2]:
                results.append((ip, int(l), 0, round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4)))
            elif p[1] > p[0] and p[1] > p[2]:
                results.append((ip, int(l), 1, round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4)))
            else:
                results.append((ip, int(l), 2, round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4)))

    # ================================== accuracy and sensitivity ==================================
    test_cm = test_cm.value()
    test_accuracy = 100. * sum([test_cm[c][c] for c in range(config.num_classes)]) / test_cm.sum()
    test_sp = [100. * (test_cm.sum() - test_cm.sum(0)[i] - test_cm.sum(1)[i] + test_cm[i][i]) / (test_cm.sum() - test_cm.sum(1)[i])
               for i in range(config.num_classes)]
    test_se = [100. * test_cm[i][i] / test_cm.sum(1)[i] for i in range(config.num_classes)]
    test_cm = test_cm / np.expand_dims(np.array(test_scale), axis=1)  # 计算指标时按照balance后的matrix来算，展示的时候还原

    # ================================ Save and Print Prediction Results ===========================
    if config.result_file:
        write_csv(os.path.join('results', config.result_file), tag=['path', 'label', 'predict', 'p1', 'p2', 'p3'], content=results)

    print('test_acc:', test_accuracy)
    print('test_sp0:', test_sp[0], 'test_sp1:', test_sp[1], 'test_sp2:', test_sp[2])
    print('test_se0:', test_se[0], 'test_se1:', test_se[1], 'test_se2:', test_se[2])
    print('test_mAP:', test_mAP.value().numpy())
    print('test_cm:')
    print(test_cm.astype(dtype=np.int32))


if __name__ == '__main__':
    fire.Fire({
        'iter_train': iter_train,
        'test_2class': test_2class,
        'test_3class': test_3class
    })
