import os.path as osp

import argparse
import torch
import torch.nn.functional as F
from torch_scatter import scatter_mean
from torch_geometric.datasets import TUDataset
from glocal_gnn import DataLoader, GraphConv

parser = argparse.ArgumentParser()
parser.add_argument('--no-train', default=False)
args = parser.parse_args()


class MyFilter(object):
    def __call__(self, data):
        return data.num_nodes >= 4 and data.num_edges >= data.num_nodes


class MyPreTransform(object):
    def __call__(self, data):
        data.x = data.x[:, -3:]  # Only use node attributes.
        return data


BATCH = 128
path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data', '1-ENZYMES')
dataset = TUDataset(
    path,
    name='ENZYMES',
    pre_transform=MyPreTransform(),
    pre_filter=MyFilter())

# perm = torch.randperm(len(dataset), dtype=torch.long)
# torch.save(perm, '/Users/rusty1s/Desktop/enzymes_perm.pt')
perm = torch.load('/Users/rusty1s/Desktop/enzymes_perm.pt')
dataset = dataset[perm]


class Net(torch.nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.conv1 = GraphConv(dataset.num_features, 64)
        self.conv2 = GraphConv(64, 128)
        self.conv3 = GraphConv(128, 256)
        self.fc1 = torch.nn.Linear(256, 128)
        # self.fc2 = torch.nn.Linear(64, 32)
        self.fc3 = torch.nn.Linear(128, dataset.num_classes)

    def reset_parameters(self):
        for (name, module) in self._modules.items():
            module.reset_parameters()

    def forward(self, data):
        data.x = F.elu(self.conv1(data.x, data.edge_index))
        data.x = F.elu(self.conv2(data.x, data.edge_index))
        data.x = F.elu(self.conv3(data.x, data.edge_index))
        x_1 = scatter_mean(data.x, data.batch, dim=0)
        x = x_1

        if args.no_train:
            x = x.detach()

        x = F.elu(self.fc1(x))
        # x = F.dropout(x, p=0.5, training=self.training)
        # x = F.elu(self.fc2(x))
        x = self.fc3(x)
        return F.log_softmax(x, dim=1)


device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = Net().to(device)


def train(epoch, loader, optimizer):
    model.train()
    loss_all = 0

    for data in loader:
        data = data.to(device)
        optimizer.zero_grad()
        loss = F.nll_loss(model(data), data.y)
        loss.backward()
        loss_all += data.num_graphs * loss.item()
        optimizer.step()
    return loss_all / len(loader.dataset)


def val(loader):
    model.eval()
    loss_all = 0

    for data in loader:
        data = data.to(device)
        loss_all += F.nll_loss(model(data), data.y, reduction='sum').item()
    return loss_all / len(loader.dataset)


def test(loader):
    model.eval()
    correct = 0

    for data in loader:
        data = data.to(device)
        pred = model(data).max(1)[1]
        correct += pred.eq(data.y).sum().item()
    return correct / len(loader.dataset)


acc = []
for i in range(10):
    model.reset_parameters()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='max', factor=0.7, patience=10, min_lr=0.00001)

    test_mask = torch.zeros(len(dataset), dtype=torch.uint8)
    n = len(dataset) // 10
    test_mask[i * n:(i + 1) * n] = 1
    test_dataset = dataset[test_mask]
    train_dataset = dataset[1 - test_mask]

    n = len(train_dataset) // 10
    val_mask = torch.zeros(len(train_dataset), dtype=torch.uint8)
    val_mask[i * n:(i + 1) * n] = 1
    val_dataset = train_dataset[val_mask]
    train_dataset = train_dataset[1 - val_mask]

    val_loader = DataLoader(val_dataset, batch_size=BATCH)
    test_loader = DataLoader(test_dataset, batch_size=BATCH)
    train_loader = DataLoader(train_dataset, batch_size=BATCH, shuffle=True)

    print('---------------- Split {} ----------------'.format(i))

    best_val_acc, test_acc = 0, 0
    for epoch in range(1, 201):
        lr = scheduler.optimizer.param_groups[0]['lr']
        train_loss = train(epoch, train_loader, optimizer)
        val_acc = test(val_loader)
        scheduler.step(val_acc)
        if val_acc >= best_val_acc:
            test_acc = test(test_loader)
            best_val_acc = val_acc
        if epoch % 5 == 0:
            print('Epoch: {:03d}, LR: {:7f}, Train Loss: {:.7f}, '
                  'Val Acc: {:.7f}, Test Acc: {:.7f}'.format(
                      epoch, lr, train_loss, val_acc, test_acc))
    acc.append(test_acc)
acc = torch.tensor(acc)
print('---------------- Final Result ----------------')
print('Mean: {:7f}, Std: {:7f}'.format(acc.mean(), acc.std()))