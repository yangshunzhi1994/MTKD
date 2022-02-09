#!/usr/bin/python3
# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import os
import sys
import time
import logging
import argparse
import numpy as np
from itertools import chain

import torch
import itertools
import torch.nn as nn
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
import torchvision.transforms as transforms
from torch.autograd import Variable
from datasets.RAF import RAF
from datasets.PET import PET
from datasets.colorferet import colorferet
from datasets.FairFace import FairFace
from network.teacherNet import Teacher
from network.studentNet import CNN_RIS
import other

import utils
from utils import load_pretrained_model, count_parameters_in_MB

import losses
from tensorboardX import SummaryWriter

parser = argparse.ArgumentParser(description='train kd')

# various path
parser.add_argument('--save_root', type=str, default='results/', help='models and logs are saved here')
parser.add_argument('--t_model', type=str, default="Teacher", help='Teacher,Teacher1,Teacher3')
parser.add_argument('--s_model', type=str, default="CNNRIS", help='name of student model')
parser.add_argument('--distillation', type=str, default="OurDiversity", help='OurDiversity,AEKD,ENDD,Few-Shot,Fukuda,AMTML-KD,FFMCD,ONE,PCL,CTR,EKD,Average')
parser.add_argument('--data_name', type=str, default='RAF', help='RAF,FairFace,colorferet,PET') 
# training hyper parameters
parser.add_argument('--epochs', type=int, default=300, help='number of total epochs to run')
parser.add_argument('--train_bs', default=32, type=int, help='learning rate')
parser.add_argument('--test_bs', default=256, type=int, help='learning rate')
parser.add_argument('--lr', type=float, default=0.01, help='initial learning rate')
parser.add_argument('--momentum', type=float, default=0.9, help='momentum')
parser.add_argument('--weight_decay', type=float, default=5e-4, help='weight decay')#1e-4,5e-4
parser.add_argument('--cuda', type=int, default=1)
parser.add_argument('--direction', default=0.0, type=float, help='direction')
parser.add_argument('--variance', default=0.0, type=float, help='variance')
parser.add_argument('--num_workers', type=int, default=1, help='num_workers')
parser.add_argument('--seed', type=int, default=2, help='random seed')
parser.add_argument('--S_size', default=44, type=int, help='44,32,24,16,8')
parser.add_argument('--noise', type=str, default='None', help='GaussianBlur,AverageBlur,MedianBlur,BilateralFilter,Salt-and-pepper') 

args, unparsed = parser.parse_known_args()

if args.distillation == 'OurDiversity':
    path = os.path.join(args.save_root + args.data_name + '_MultiTeacher_OurDiversity_' + str(args.direction)+ '_' + str(args.variance)+ '_KD3')
else:
    path = os.path.join(args.save_root + args.data_name+ '_MultiTeacher_' + args.distillation+ '_KD3')
writer = SummaryWriter(log_dir=path)

np.random.seed(args.seed)
torch.manual_seed(args.seed)
if args.cuda:
    torch.cuda.manual_seed(args.seed)
    cudnn.enabled = True
    cudnn.benchmark = True
else:
    pass

best_acc = 0
best_mAP = 0
best_F1 = 0
learning_rate_decay_start = 80  # 50
learning_rate_decay_every = 5 # 5
learning_rate_decay_rate = 0.9 # 0.9
if args.data_name == 'colorferet':
    NUM_CLASSES = 994
elif args.data_name == 'PET':
    NUM_CLASSES = 37
else:
    NUM_CLASSES = 7

if args.s_model == 'CNNRIS':
    snet = CNN_RIS(num_classes=NUM_CLASSES, S_size=args.S_size)
else:
    raise Exception('Invalid name of the student network...')

if args.t_model == 'Teacher':
    tnet1 = Teacher(num_classes=NUM_CLASSES)
    tnet2 = Teacher(num_classes=NUM_CLASSES)
    tnet3 = Teacher(num_classes=NUM_CLASSES)
    tnet4 = Teacher(num_classes=NUM_CLASSES)
else:
    raise Exception('Invalid name of the teacher network...')

if args.distillation == 'OurDiversity':
    tcheckpoint = torch.load(os.path.join(args.save_root + args.data_name + '_MultiTeacher_OurDiversity_' + \
                                          str(args.direction)+ '_' + str(args.variance),'Best_MultiTeacher_model.t7'))
elif args.distillation == 'CTR' or args.distillation == 'AMTML-KD'or args.distillation == 'Average':
    tcheckpoint = torch.load(os.path.join('results/' + args.data_name+ '_MultiTeacher_OurDiversity_0.0_0.0', 'Best_MultiTeacher_model.t7'))
else:
    tcheckpoint = torch.load(os.path.join('results/' + args.data_name+ '_MultiTeacher_'+ \
                                         args.distillation,'Best_MultiTeacher_model.t7')) 
load_pretrained_model(tnet1, tcheckpoint['Teacher1'])
load_pretrained_model(tnet2, tcheckpoint['Teacher2'])
load_pretrained_model(tnet3, tcheckpoint['Teacher3'])

if args.distillation == 'Fukuda':
    tcheckpoint4 = torch.load('results/RAF_Teacher_False/Best_Teacher_model.t7')
    load_pretrained_model(tnet4, tcheckpoint4['tnet'])
else:
    load_pretrained_model(tnet4, tcheckpoint['Teacher4'])


print ('The dataset used for training is:   '+ str(args.data_name))
print ('The distillation method is:       '+ str(args.distillation))
print ('The type of noise used is:        '+ str(args.noise))
print ('Resolution of the student network:        '+ str(args.S_size))
print ('best_Teacher1_acc is '+ str(tcheckpoint['test_Teacher1_accuracy']))  
print ('best_Teacher2_acc is '+ str(tcheckpoint['test_Teacher2_accuracy'])) 
print ('best_Teacher3_acc is '+ str(tcheckpoint['test_Teacher3_accuracy'])) 

if args.distillation == 'Fukuda':
    print ('best_Teacher4_acc is 87.94 / '+ str(tcheckpoint4['best_PrivateTest_acc'])) 
else:
    print ('best_Teacher4_acc is '+ str(tcheckpoint['test_Teacher4_accuracy'])) 

print ('best_Teacher_Avg_accuracy is '+ str(tcheckpoint['test_Avg_accuracy'])) 
print ('best_Teacher_Avg_MAP is '+ str(tcheckpoint['test_Avg_MAP'])) 
print ('best_Teacher_Avg_F1 is '+ str(tcheckpoint['test_Avg_F1'])) 

tnet1.eval()
for param in tnet1.parameters():
    param.requires_grad = False
tnet2.eval()
for param in tnet2.parameters():
    param.requires_grad = False
tnet3.eval()
for param in tnet3.parameters():
    param.requires_grad = False
tnet4.eval()
for param in tnet4.parameters():
    param.requires_grad = False
    
# define loss functions
if args.cuda:
    Cls_crit = torch.nn.CrossEntropyLoss().cuda()
    tnet1.cuda()
    tnet2.cuda()
    tnet3.cuda()
    tnet4.cuda()
    snet.cuda()
    
if args.distillation == 'AMTML-KD':
    W = torch.randn(272, 1).cuda()
    W.requires_grad_(True)
    optimizer = torch.optim.SGD(itertools.chain([W],snet.parameters()), lr = args.lr, momentum = args.momentum,
                                weight_decay = args.weight_decay,nesterov = True)
elif args.distillation == 'OKDDip' or args.distillation == 'ONE' or args.distillation == 'CTR':
    if args.distillation == 'OKDDip':
        net = other.OKDDip_Student(in_dim=NUM_CLASSES,out_dim=NUM_CLASSES).cuda()
    elif args.distillation == 'ONE':
        net = other.ONE(in_dim=NUM_CLASSES,out_dim=NUM_CLASSES).cuda()
    else:
        net = other.CTR(dim=NUM_CLASSES).cuda()
    optimizer = torch.optim.SGD(itertools.chain(snet.parameters(),net.parameters()), lr = args.lr, momentum = args.momentum,
                                weight_decay = args.weight_decay,nesterov = True)
else:
    optimizer = torch.optim.SGD(snet.parameters(), lr = args.lr, momentum = args.momentum,
                                weight_decay = args.weight_decay,nesterov = True)

transform_train = transforms.Compose([
    transforms.RandomCrop(92),
    transforms.RandomHorizontalFlip(),
])

if args.data_name == 'RAF':
	transforms_teacher_Normalize = transforms.Normalize((0.5884594, 0.45767313, 0.40865755), 
                            (0.25717735, 0.23602168, 0.23505741))
	transforms_student_Normalize =  transforms.Normalize((0.58846486, 0.45766878, 0.40865615), 
                            (0.2516557, 0.23020789, 0.22939532))
	transforms_test_Normalize = transforms.Lambda(lambda crops: torch.stack([transforms.Normalize(
            mean=[0.59003043, 0.4573948, 0.40749523], std=[0.2465465, 0.22635746, 0.22564183])
            (transforms.ToTensor()(crop)) for crop in crops]))
elif args.data_name == 'PET':
	transforms_teacher_Normalize = transforms.Normalize((0.47950855, 0.4454716, 0.3953508), 
                            (0.26221144, 0.25676072, 0.2640482))
	transforms_student_Normalize =  transforms.Normalize((0.4794851, 0.44543326, 0.39531776), 
                            (0.24786888, 0.24236518, 0.24950708))
	transforms_test_Normalize = transforms.Lambda(lambda crops: torch.stack([transforms.Normalize(
            mean=[0.4862494, 0.45275217, 0.39576027], std=[0.24864933, 0.2446337, 0.2527274])
            (transforms.ToTensor()(crop)) for crop in crops]))
elif args.data_name == 'colorferet':
	transforms_teacher_Normalize = transforms.Normalize((0.50150657, 0.4387828, 0.37715995), 
                            (0.22249317, 0.24526535, 0.25831717))
	transforms_student_Normalize =  transforms.Normalize((0.50166893, 0.43892872, 0.37727863), 
                            (0.21588857, 0.23875234, 0.25212118))
	transforms_test_Normalize = transforms.Lambda(lambda crops: torch.stack([transforms.Normalize(
            mean=[0.4992823, 0.4371743, 0.37574747], std=[0.21377444, 0.23534843, 0.24466512])
            (transforms.ToTensor()(crop)) for crop in crops]))
elif args.data_name == 'FairFace':
	transforms_teacher_Normalize = transforms.Normalize((0.4911152, 0.36028033, 0.30489963), 
                            (0.25160596, 0.21829675, 0.21198231))
	transforms_student_Normalize =  transforms.Normalize((0.4911364, 0.3602937, 0.3049148), 
                            (0.24722975, 0.21383813, 0.20771481))
	transforms_test_Normalize = transforms.Lambda(lambda crops: torch.stack([transforms.Normalize(
            mean=[0.49202734, 0.36110377, 0.30535242], std=[0.24179104, 0.21022305, 0.20413795])
            (transforms.ToTensor()(crop)) for crop in crops]))
else:
    raise Exception('Invalid dataset name...')

teacher_norm = transforms.Compose([
transforms.ToTensor(),
transforms_teacher_Normalize,
])

student_norm = transforms.Compose([
transforms.Resize(args.S_size),
transforms.ToTensor(),
transforms_student_Normalize,
])

transform_test = transforms.Compose([
transforms.TenCrop(args.S_size),
transforms_test_Normalize,
])

if args.data_name == 'RAF':
	trainset = RAF(split = 'Training', transform=transform_train, student_norm=student_norm, teacher_norm=teacher_norm, S_size=args.S_size)
	PrivateTestset = RAF(split = 'PrivateTest', transform=transform_test, student_norm=None, teacher_norm=None, S_size=args.S_size)
elif args.data_name == 'PET':
	trainset = PET(split = 'Training', transform=transform_train, student_norm=student_norm, teacher_norm=teacher_norm, noise=args.noise)
	PrivateTestset = PET(split = 'PrivateTest', transform=transform_test, student_norm=None, teacher_norm=None, noise=args.noise)
elif args.data_name == 'colorferet':
	trainset = colorferet(split = 'Training', transform=transform_train, student_norm=student_norm, teacher_norm=teacher_norm, noise=args.noise)
	PrivateTestset = colorferet(split = 'PrivateTest', transform=transform_test, student_norm=None, teacher_norm=None, noise=args.noise)
elif args.data_name == 'FairFace':
	trainset = FairFace(split = 'Training', transform=transform_train, student_norm=student_norm, teacher_norm=teacher_norm, noise=args.noise)
	PrivateTestset = FairFace(split = 'PrivateTest', transform=transform_test, student_norm=None, teacher_norm=None, noise=args.noise)
else:
	raise Exception('Invalid dataset name...')

trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.train_bs, shuffle=True, num_workers=args.num_workers)
PrivateTestloader = torch.utils.data.DataLoader(PrivateTestset, batch_size=args.test_bs, shuffle=False, num_workers=args.num_workers)

def train(epoch):
    print('\nEpoch: %d' % epoch)
    snet.train()
    if args.distillation == 'OKDDip' or args.distillation == 'CTR' or args.distillation == 'ONE': 
        net.train()
    else:
        pass
    train_loss = 0
    train_cls_loss = 0
    
    conf_mat = np.zeros((NUM_CLASSES, NUM_CLASSES))
    if epoch > learning_rate_decay_start and learning_rate_decay_start >= 0:
        frac = (epoch - learning_rate_decay_start) // learning_rate_decay_every
        decay_factor = learning_rate_decay_rate ** frac
        current_lr = args.lr * decay_factor
        utils.set_lr(optimizer, current_lr)  # set the decayed rate
    else:
        current_lr = args.lr
    print('learning_rate: %s' % str(current_lr))

    for batch_idx, (img_teacher, img_student, target) in enumerate(trainloader):
        if args.cuda:
            img_teacher = img_teacher.cuda()
            img_student = img_student.cuda()
            target = target.cuda()

        optimizer.zero_grad()
        img_teacher, img_student, target = Variable(img_teacher), Variable(img_student), Variable(target)
        
        rb1_s, rb2_s, rb3_s, mimic_s, out_s = snet(img_student)
        with torch.no_grad():
            rb1_t1, rb2_t1, rb3_t1, mimic_t1, out_t1 = tnet1(img_teacher)
            rb1_t2, rb2_t2, rb3_t2, mimic_t2, out_t2 = tnet2(img_teacher)
            rb1_t3, rb2_t3, rb3_t3, mimic_t3, out_t3 = tnet3(img_teacher)
            rb1_t4, rb2_t4, rb3_t4, mimic_t4, out_t4 = tnet4(img_teacher)
        
        cls_loss = Cls_crit(out_s, target)
        if args.distillation == 'OurDiversity':
            loss = losses.Dynamic_MultiTeacher().cuda()(out_t1, out_t2, out_t3, out_t4, out_s, target)
        elif args.distillation == 'Average':
            mimic = (out_t1+out_t2+out_t3+out_t4)/4
            loss = 0.2*cls_loss + 0.8*other.KL_divergence(temperature = 20).cuda()(mimic,out_s)
        elif args.distillation == 'USTE':
            mimic = other.USTE_prediction(temperature = 1).cuda()(out_t1, out_t2, out_t3, out_t4)
            loss = 0.1*cls_loss + 0.9*other.KL_divergence(temperature = 6).cuda()(mimic,out_s)
        elif args.distillation == 'AEKD': #  Agree to Disagree: Adaptive Ensemble Knowledge Distillation in Gradient Space
            loss_div = other.AEKD().cuda()(out_t1,out_t2,out_t3,out_t4, out_s)
            loss = cls_loss + 0.9 * loss_div
        elif args.distillation == 'ENDD':
            loss = other.ENDD().cuda()(out_t1, out_t2, out_t3, out_t4, out_s)
        elif args.distillation == 'Few-Shot':
            mimic = (out_t1+out_t2+out_t3+out_t4)/4
            loss = 0.2*cls_loss + 0.8*other.KL_divergence(temperature = 10).cuda()(mimic,out_s)
        elif args.distillation == 'Fukuda':
            #Generalized Knowledge Distillation from an Ensemble of Specialized Teachers Leveraging Unsupervised Neural Clustering
            loss1 = - torch.sum(F.softmax(out_t1,dim=1) * F.log_softmax(out_s,dim=1), 1, keepdim=False)
            loss2 = - torch.sum(F.softmax(out_t2,dim=1) * F.log_softmax(out_s,dim=1), 1, keepdim=False)
            loss3 = - torch.sum(F.softmax(out_t3,dim=1) * F.log_softmax(out_s,dim=1), 1, keepdim=False)
            loss4 = - torch.sum(F.softmax(out_t4,dim=1) * F.log_softmax(out_s,dim=1), 1, keepdim=False)
            loss = 0.16*loss1 + 0.16*loss2 + 0.16*loss3 + 0.5*loss4
            loss = loss.mean()
        elif args.distillation == 'AMTML-KD': #  daptive Multi-Teacher Multi-level Knowledge Distillation
            alpha = torch.cat((mimic_t1.mm(W).unsqueeze(0), mimic_t2.mm(W).unsqueeze(0), mimic_t3.mm(W).unsqueeze(0), \
                        mimic_t4.mm(W).unsqueeze(0)),0)
            alpha = alpha.squeeze(2).transpose(0, 1)
            weight = F.softmax(alpha)
            weight = torch.unsqueeze(weight, dim=2)
            te_scores_Tensor = torch.cat((out_t1.unsqueeze(1), out_t2.unsqueeze(1), out_t3.unsqueeze(1), out_t4.unsqueeze(1)),1)
            weighted_logits = weight * te_scores_Tensor 
            weighted_logits = torch.sum(weighted_logits, dim=1)
            loss = other.AMTML_KD().cuda()(out_s, target, weighted_logits, T=5.0, alpha=0.7)
        elif args.distillation == 'FFMCD':#Online Knowledge Distillation via Multi-branch Diversity Enhancement
            mimic = (out_t1+out_t2+out_t3+out_t4)/4
            loss = cls_loss + 2*other.KL_divergence(temperature = 3).cuda()(mimic,out_s)
        elif args.distillation == 'OKDDip':#Online Knowledge Distillation with Diverse Peers
            L_dis = net(out_t1,out_t2,out_t3,out_t4,out_s)
            loss = cls_loss + L_dis
        elif args.distillation == 'ONE':# Knowledge Distillation by On-the-Fly Native Ensemble
            mimic = net(out_t1,out_t2,out_t3,out_t4)
            loss = cls_loss + other.KL_divergence(temperature = 3).cuda()(mimic,out_s)
        elif args.distillation == 'PCL':# Peer Collaborative Learning for Online Knowledge Distillation
            loss_pm1 = other.sigmoid_rampup(epoch, 80)*other.KL_divergence(temperature = 3).cuda()(out_s,out_t1)
            loss_pm2 = other.sigmoid_rampup(epoch, 80)*other.KL_divergence(temperature = 3).cuda()(out_s,out_t2)
            loss_pm3 = other.sigmoid_rampup(epoch, 80)*other.KL_divergence(temperature = 3).cuda()(out_s,out_t3)
            loss_pm4 = other.sigmoid_rampup(epoch, 80)*other.KL_divergence(temperature = 3).cuda()(out_s,out_t4)
            loss = cls_loss + (loss_pm1+loss_pm2+loss_pm3+loss_pm4)/4
        elif args.distillation == 'CTR':# Ensembled CTR Prediction via Knowledge Distillation
            mimic = net(out_t1,out_t2,out_t3,out_t4)
            loss = 0.2*cls_loss + 0.8*other.KL_divergence(temperature = 20).cuda()(mimic,out_s)
        elif args.distillation == 'EKD':#Ensemble Knowledge Distillation for Learning Improved and Efficient Networks
            mimic = out_t1+out_t2+out_t3+out_t4
            EKD_loss = other.EKD().cuda()(out_t1,out_t2,out_t3,out_t4,mimic,out_s)
            loss = 0.5*cls_loss+0.6*EKD_loss
        else:
            raise Exception('Invalid distillation name...')
        loss.backward()
        utils.clip_gradient(optimizer, 0.1)
        optimizer.step()
        train_loss += loss.item()
        train_cls_loss += cls_loss.item()
        
        conf_mat += losses.confusion_matrix(out_s, target, NUM_CLASSES)
        acc = sum([conf_mat[i, i] for i in range(conf_mat.shape[0])])/conf_mat.sum()
        precision = [conf_mat[i, i]/(conf_mat[i].sum() + 1e-10) for i in range(conf_mat.shape[0])]
        mAP = sum(precision)/len(precision)
        
        recall = [conf_mat[i, i]/(conf_mat[:, i].sum() + 1e-10) for i in range(conf_mat.shape[0])]
        precision = np.array(precision)
        recall = np.array(recall)
        f1 = 2 * precision*recall/(precision+recall + 1e-10)
        F1_score = f1.mean()
        
#         utils.progress_bar(batch_idx, len(trainloader), 'Loss: %.3f | Acc: %.3f%% | mAP: %.3f%% | F1: %.3f%%'
#                            % (train_loss/(batch_idx+1), 100.*acc, 100.* mAP, 100.* F1_score))
    
    return train_cls_loss/(batch_idx+1), 100.*acc, 100.* mAP, 100 * F1_score

def test(epoch):
    snet.eval()
    if args.distillation == 'OKDDip' or args.distillation == 'CTR' or args.distillation == 'ONE': 
        net.eval()
    else:
        pass
    PrivateTest_loss = 0
    t_prediction = 0
    conf_mat = np.zeros((NUM_CLASSES, NUM_CLASSES))
    for batch_idx, (img, target) in enumerate(PrivateTestloader):
        t = time.time()
        test_bs, ncrops, c, h, w = np.shape(img)
        img = img.view(-1, c, h, w)
        if args.cuda:
            img = img.cuda()
            target = target.cuda()
        img, target = Variable(img), Variable(target)
        with torch.no_grad():
            rb1_s, rb2_s, rb3_s, mimic_s, out_s = snet(img)
            
        outputs_avg = out_s.view(test_bs, ncrops, -1).mean(1)
        loss = Cls_crit(outputs_avg, target)
        t_prediction += (time.time() - t)
        PrivateTest_loss += loss.item()
        
        conf_mat += losses.confusion_matrix(outputs_avg, target, NUM_CLASSES)
        acc = sum([conf_mat[i, i] for i in range(conf_mat.shape[0])])/conf_mat.sum()
        precision = [conf_mat[i, i]/(conf_mat[i].sum() + 1e-10) for i in range(conf_mat.shape[0])]
        mAP = sum(precision)/len(precision)
        
        recall = [conf_mat[i, i]/(conf_mat[:, i].sum() + 1e-10) for i in range(conf_mat.shape[0])]
        precision = np.array(precision)
        recall = np.array(recall)
        f1 = 2 * precision*recall/(precision+recall + 1e-10)
        F1_score = f1.mean()
        
#         utils.progress_bar(batch_idx, len(PrivateTestloader), 'Loss: %.3f | Acc: %.3f%% | mAP: %.3f%% | F1: %.3f%%'
#                            % (PrivateTest_loss / (batch_idx + 1), 100.*acc, 100.* mAP, 100.* F1_score))
  
    return PrivateTest_loss/(batch_idx+1), 100.*acc, 100.* mAP, 100 * F1_score

for epoch in range(1, args.epochs+1):
    train_loss, train_acc, train_mAP, train_F1 = train(epoch)
    test_loss, test_acc, test_mAP, test_F1 = test(epoch)
    print("train_loss:  %0.3f, train_acc:  %0.3f, train_mAP:  %0.3f, train_F1:  %0.3f"%
          (train_loss, train_acc, train_mAP, train_F1))
    print("test_loss:   %0.3f, test_acc:   %0.3f, test_mAP:   %0.3f, test_F1:   %0.3f"%
          (test_loss, test_acc, test_mAP, test_F1))
    writer.add_scalars('epoch/loss', {'train': train_loss, 'test': test_loss}, epoch)
    writer.add_scalars('epoch/accuracy', {'train': train_acc, 'test': test_acc}, epoch)
    writer.add_scalars('epoch/mAP', {'train': train_mAP, 'test': test_mAP}, epoch)
    writer.add_scalars('epoch/F1', {'train': train_F1, 'test': test_F1}, epoch)
    
    if test_acc > best_acc:
        best_acc = test_acc
        best_mAP = test_mAP
        best_F1 = test_F1
        print ('Saving models......')
        print("best_PrivateTest_acc: %0.3f" % best_acc)
        print("best_PrivateTest_mAP: %0.3f" % best_mAP)
        print("best_PrivateTest_F1: %0.3f" % best_F1)
        state = {
            'epoch': epoch,
            'snet': snet.state_dict() if args.cuda else snet,
            'test_acc': test_acc,
            'test_mAP': test_mAP,
            'test_F1': test_F1,
            'test_epoch': epoch,
        } 
        torch.save(state, os.path.join(path,'Student_Test_model.t7'))

print("best_PrivateTest_acc: %0.3f" % best_acc)
print("best_PrivateTest_mAP: %0.3f" % best_mAP)
print("best_PrivateTest_F1: %0.3f" % best_F1)



